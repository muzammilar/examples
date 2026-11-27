#include <nlohmann/json.hpp>
#include <openssl/evp.h>

#include <array>
#include <cerrno>
#include <chrono>
#include <cstdlib>
#include <fcntl.h>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <sys/file.h>
#include <sys/resource.h>
#include <sys/vfs.h>
#include <system_error>
#include <thread>
#include <unistd.h>
#include <vector>

using json = nlohmann::json;
namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;

void check(bool ok, const std::string &operation) {
  if (!ok)
    throw std::system_error(errno, std::generic_category(), operation);
}
long long now_ms() {
  return std::chrono::duration_cast<std::chrono::milliseconds>(
             std::chrono::system_clock::now().time_since_epoch())
      .count();
}
void emit(json row) {
  row["utc_ms"] = now_ms();
  std::cout << row.dump() << std::endl;
}
class File {
  int fd_;

public:
  File(const fs::path &path, int flags)
      : fd_(::open(path.c_str(), flags, 0600)) {
    check(fd_ >= 0, "open " + path.string());
  }
  File(const File &) = delete;
  File &operator=(const File &) = delete;
  ~File() {
    if (fd_ >= 0)
      ::close(fd_);
  }
  int get() const { return fd_; }
  void close() {
    const int old = fd_;
    fd_ = -1;
    check(::close(old) == 0, "close");
  }
};
void write_all(int fd, const char *data, std::size_t size) {
  std::size_t done = 0;
  while (done < size) {
    auto n = ::write(fd, data + done, size - done);
    if (n < 0 && errno == EINTR)
      continue;
    check(n > 0, "write");
    done += static_cast<std::size_t>(n);
  }
}
void sync(int fd) { check(::fsync(fd) == 0, "fsync"); }
std::vector<char> payload(const std::string &run, std::size_t seq,
                          std::size_t size) {
  auto seed =
      std::string(PAYLOAD_SEED_PREFIX) + ":" + run + ":" + std::to_string(seq);
  std::vector<char> data(size);
  std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)> ctx(EVP_MD_CTX_new(),
                                                              EVP_MD_CTX_free);
  if (!ctx || EVP_DigestInit_ex(ctx.get(), EVP_shake256(), nullptr) != 1 ||
      EVP_DigestUpdate(ctx.get(), seed.data(), seed.size()) != 1 ||
      EVP_DigestFinalXOF(
          ctx.get(), reinterpret_cast<unsigned char *>(data.data()), size) != 1)
    throw std::runtime_error("SHAKE256");
  return data;
}
std::string sha256(const std::vector<char> &data) {
  std::array<unsigned char, EVP_MAX_MD_SIZE> digest{};
  unsigned int size = 0;
  if (EVP_Digest(data.data(), data.size(), digest.data(), &size, EVP_sha256(),
                 nullptr) != 1)
    throw std::runtime_error("SHA256");
  std::ostringstream out;
  for (unsigned int i = 0; i < size; ++i)
    out << std::hex << std::setw(2) << std::setfill('0') << unsigned(digest[i]);
  return out.str();
}
fs::path witness_root() {
  const char *configured = std::getenv("WITNESS_ROOT");
  return configured ? fs::path(configured) : fs::path("/results");
}
fs::path journal(const std::string &run) {
  return witness_root() / "journals" / (run + ".jsonl");
}
std::map<std::size_t, json> records(const std::string &run,
                                    const std::string &event) {
  std::ifstream input(journal(run));
  if (!input)
    throw std::runtime_error("Missing external witness");
  std::map<std::size_t, json> rows;
  std::string line;
  while (std::getline(input, line)) {
    if (input.eof())
      throw std::runtime_error("Truncated witness");
    auto row = json::parse(line);
    if (row.at("run") != run)
      throw std::runtime_error("Foreign witness");
    if (row.at("event") == event &&
        !rows.emplace(row.at("sequence").get<std::size_t>(), row).second)
      throw std::runtime_error("Duplicate witness");
  }
  return rows;
}
void verify(const std::string &run) {
  const auto attempts = records(run, "attempt"), acks = records(run, "ack");
  if (acks.empty())
    throw std::runtime_error("Empty ACK set");
  for (const auto &[seq, row] : acks) {
    if (!attempts.count(seq) ||
        row.at("sha256") != attempts.at(seq).at("sha256") ||
        row.at("bytes") != attempts.at(seq).at("bytes"))
      throw std::runtime_error("ACK differs from intent");
    const auto size = row.at("bytes").get<std::size_t>();
    if (!size || size > 1048576 ||
        row.at("sha256") != sha256(payload(run, seq, size)))
      throw std::runtime_error("Witness differs from deterministic oracle");
    const auto path = fs::path("/mnt/lab") / run / std::to_string(seq);
    const auto started = Clock::now();
    File file(path, O_RDONLY);
    std::vector<char> data(size);
    std::size_t done = 0;
    while (done < size) {
      auto n = ::read(file.get(), data.data() + done, size - done);
      if (n < 0 && errno == EINTR)
        continue;
      check(n >= 0, "read");
      if (!n)
        throw std::runtime_error("Short successful body");
      done += static_cast<std::size_t>(n);
    }
    char extra = 0;
    auto n = ::read(file.get(), &extra, 1);
    check(n >= 0, "read EOF");
    if (n != 0 || sha256(data) != row.at("sha256"))
      throw std::runtime_error("Wrong successful body");
    file.close();
    emit({{"event", "body_read"},
          {"run", run},
          {"sequence", seq},
          {"bytes", size},
          {"duration_ms",
           std::chrono::duration<double, std::milli>(Clock::now() - started)
               .count()}});
  }
  emit({{"event", "verified"},
        {"run", run},
        {"acks", acks.size()},
        {"wrong", 0},
        {"missing_ack", 0}});
}
void seed(const std::string &run, std::size_t count, std::size_t size,
          bool live = false) {
  if (!count || count > 1024 || !size || size > 1048576 ||
      count * size > 268435456)
    throw std::invalid_argument("Seed exceeds bounds");
  fs::create_directories(witness_root() / "journals");
  File witness(journal(run), O_WRONLY | O_CREAT | O_EXCL | O_APPEND);
  const auto directory = fs::path("/mnt/lab") / run;
  if (!fs::create_directory(directory))
    throw std::runtime_error("Run already exists");
  for (std::size_t seq = 0; seq < count; ++seq) {
    if (live && fs::exists(witness_root() / ("stop-writer-" + run)))
      break;
    auto data = payload(run, seq, size);
    json row{{"run", run},         {"sequence", seq},
             {"bytes", size},      {"sha256", sha256(data)},
             {"event", "attempt"}, {"utc_start_ms", now_ms()}};
    auto line = row.dump() + "\n";
    write_all(witness.get(), line.data(), line.size());
    sync(witness.get());
    const auto start = Clock::now();
    try {
      auto temporary = directory / ("." + std::to_string(seq) + ".tmp");
      File out(temporary, O_WRONLY | O_CREAT | O_EXCL);
      write_all(out.get(), data.data(), data.size());
      sync(out.get());
      out.close();
      fs::rename(temporary, directory / std::to_string(seq));
      File parent(directory, O_RDONLY | O_DIRECTORY);
      sync(parent.get());
      if (SYNC_PUBLICATION_WITH_SYNCFS)
        check(::syncfs(parent.get()) == 0, "syncfs publication");
      parent.close();
      row["event"] = "ack";
    } catch (const std::exception &e) {
      row["event"] = "ambiguous";
      row["error"] = e.what();
    }
    row["duration_ms"] =
        std::chrono::duration<double, std::milli>(Clock::now() - start).count();
    row["utc_end_ms"] = now_ms();
    line = row.dump() + "\n";
    write_all(witness.get(), line.data(), line.size());
    sync(witness.get());
    emit(row);
    if (row["event"] != "ack" && !live)
      throw std::runtime_error("Publication ambiguous; preserve state");
    if (live)
      std::this_thread::sleep_for(std::chrono::milliseconds(100));
  }
  witness.close();
}
void lock_probe(const std::string &mode, const std::string &run) {
  const auto path = fs::path("/mnt/lab") / (run + ".lock");
  if (mode == "lock-hold") {
    File create(path, O_WRONLY | O_CREAT | O_EXCL);
    sync(create.get());
    create.close();
  }
  const auto opening = Clock::now();
  // Linux flock supports exclusive locks on read-only descriptors. Avoid
  // conflating SeaweedFS write-open ownership with the lock primitive.
  File file(path, O_RDONLY);
  emit({{"event", "lock_file_opened"},
        {"open_ms",
         std::chrono::duration<double, std::milli>(Clock::now() - opening)
             .count()}});
  if (mode == "lock-hold") {
    check(::flock(file.get(), LOCK_EX | LOCK_NB) == 0, "acquire holder lock");
    emit({{"event", "lock_held"}});
    std::this_thread::sleep_for(std::chrono::seconds(20));
  } else {
    const auto started = Clock::now();
    int result = ::flock(file.get(), LOCK_EX | LOCK_NB);
    if (result == 0)
      throw std::runtime_error("Lock incorrectly admitted across mounts");
    check(errno == EWOULDBLOCK || errno == EAGAIN, "lock contender");
    emit({{"event", "lock_denied"},
          {"flock_ms",
           std::chrono::duration<double, std::milli>(Clock::now() - started)
               .count()}});
  }
}
int main(int argc, char **argv) {
  try {
    if (argc < 3)
      throw std::invalid_argument("mode run [count bytes]");
    std::string mode = argv[1], run = argv[2];
    if (run.size() != 32 ||
        run.find_first_not_of("0123456789abcdef") != std::string::npos)
      throw std::invalid_argument("Invalid run identity");
    struct statfs filesystem{};
    check(::statfs("/mnt/lab", &filesystem) == 0, "statfs");
    if (filesystem.f_type != EXPECTED_FILESYSTEM_MAGIC)
      throw std::runtime_error("Unexpected filesystem type");
    emit({{"event", "mount_verified"}, {"f_type", filesystem.f_type}});
    emit({{"event", "source"}, {"sha256", WORKLOAD_SOURCE_SHA256}});
    emit({{"event", "publication_contract"},
          {"syncfs", bool(SYNC_PUBLICATION_WITH_SYNCFS)},
          {"payload_seed_prefix", PAYLOAD_SEED_PREFIX}});
    if ((mode == "seed" || mode == "writer") && argc == 5)
      seed(run, std::stoull(argv[3]), std::stoull(argv[4]), mode == "writer");
    else if (mode == "verify" && argc == 3)
      verify(run);
    else if (mode == "reader" && argc == 4) {
      std::string actor = argv[3];
      if (actor.size() != 32 ||
          actor.find_first_not_of("0123456789abcdef") != std::string::npos)
        throw std::invalid_argument("Invalid reader identity");
      const auto end = Clock::now() + std::chrono::seconds(180);
      while (Clock::now() < end &&
             !fs::exists(witness_root() / ("stop-reader-" + actor))) {
        const auto start = Clock::now();
        try {
          verify(run);
        } catch (const std::system_error &error) {
          const int code = error.code().value();
          if (code != EIO && code != ETIMEDOUT && code != ECONNRESET &&
              code != EHOSTUNREACH && code != ECONNREFUSED && code != EAGAIN &&
              code != ENETUNREACH)
            throw;
          emit({{"event", "read_cycle_fail"},
                {"errno", code},
                {"error", error.what()},
                {"duration_ms",
                 std::chrono::duration<double, std::milli>(Clock::now() - start)
                     .count()}});
          std::this_thread::sleep_for(std::chrono::milliseconds(100));
          continue;
        }
        emit({{"event", "read_cycle"},
              {"duration_ms",
               std::chrono::duration<double, std::milli>(Clock::now() - start)
                   .count()}});
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
      }
    } else if ((mode == "lock-hold" || mode == "lock-try") && argc == 3)
      lock_probe(mode, run);
    else
      throw std::invalid_argument("Unknown mode/arguments");
    rusage usage{};
    check(getrusage(RUSAGE_SELF, &usage) == 0, "rusage");
    emit({{"event", "resources"},
          {"peak_rss_kib", usage.ru_maxrss},
          {"user_cpu_s", usage.ru_utime.tv_sec + usage.ru_utime.tv_usec / 1e6},
          {"system_cpu_s",
           usage.ru_stime.tv_sec + usage.ru_stime.tv_usec / 1e6}});
  } catch (const std::exception &e) {
    emit({{"event", "fatal"}, {"error", e.what()}});
    return 1;
  }
}
