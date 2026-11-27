#include <cephfs/libcephfs.h>
#include <nlohmann/json.hpp>
#include <openssl/evp.h>

#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstdlib>
#include <fcntl.h>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <map>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <sys/resource.h>
#include <system_error>
#include <thread>
#include <unistd.h>
#include <vector>

using json = nlohmann::json;
using Clock = std::chrono::steady_clock;
namespace fs = std::filesystem;
volatile std::sig_atomic_t running = 1;
void stop(int) { running = 0; }
long long epoch_ms() {
  return std::chrono::duration_cast<std::chrono::milliseconds>(
             std::chrono::system_clock::now().time_since_epoch())
      .count();
}
void emit(json value) {
  value["utc_ms"] = epoch_ms();
  std::cout << value.dump() << std::endl;
}
int checked(int result, const std::string &operation) {
  if (result < 0)
    throw std::system_error(-result, std::generic_category(), operation);
  return result;
}
void posix_check(bool success, const std::string &operation) {
  if (!success)
    throw std::system_error(errno, std::generic_category(), operation);
}
std::string contents(const fs::path &path) {
  std::ifstream stream(path);
  return {std::istreambuf_iterator<char>(stream),
          std::istreambuf_iterator<char>()};
}
std::size_t number(const char *key, std::size_t fallback, std::size_t maximum) {
  const char *configured = std::getenv(key);
  if (!configured)
    return fallback;
  std::size_t end = 0;
  auto value = std::stoull(configured, &end);
  if (end != std::string(configured).size() || !value || value > maximum)
    throw std::invalid_argument(key);
  return value;
}
class Connection {
public:
  ceph_mount_info *handle = nullptr;
  explicit Connection(const std::string &id) {
    checked(ceph_create(&handle, id.c_str()), "create native client");
    try {
      checked(ceph_conf_read_file(handle, "/etc/ceph/ceph.conf"),
              "read owned config");
      checked(ceph_conf_set(handle, "ms_client_mode", "secure"),
              "require encrypted client transport");
      std::array<char, 128> mode{};
      checked(ceph_conf_get(handle, "ms_client_mode", mode.data(), mode.size()),
              "read client transport mode");
      emit(
          {{"event", "client_configuration"}, {"ms_client_mode", mode.data()}});
      checked(
          ceph_conf_set(handle, "keyring",
                        ("/etc/ceph/ceph.client." + id + ".keyring").c_str()),
          "set owned keyring");
      checked(ceph_select_filesystem(handle, "labfs"),
              "select owned filesystem");
      checked(ceph_mount(handle, "/"), "mount native CephFS client");
    } catch (...) {
      ceph_release(handle);
      handle = nullptr;
      throw;
    }
  }
  Connection(const Connection &) = delete;
  Connection &operator=(const Connection &) = delete;
  ~Connection() {
    if (handle) {
      ceph_unmount(handle);
      ceph_release(handle);
    }
  }
  void close() {
    checked(ceph_unmount(handle), "unmount client");
    checked(ceph_release(handle), "release client");
    handle = nullptr;
  }
};
class File {
  ceph_mount_info *mount;
  int descriptor;

public:
  File(ceph_mount_info *connection, const std::string &path, int flags)
      : mount(connection),
        descriptor(checked(ceph_open(connection, path.c_str(), flags, 0600),
                           "open " + path)) {}
  File(const File &) = delete;
  File &operator=(const File &) = delete;
  ~File() {
    if (descriptor >= 0)
      ceph_close(mount, descriptor);
  }
  int get() const { return descriptor; }
  void close() {
    int fd = descriptor;
    descriptor = -1;
    checked(ceph_close(mount, fd), "close file");
  }
};
class Journal {
  int descriptor;

public:
  explicit Journal(const fs::path &path)
      : descriptor(::open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_APPEND,
                          0600)) {
    posix_check(descriptor >= 0, "create witness");
  }
  Journal(const Journal &) = delete;
  Journal &operator=(const Journal &) = delete;
  ~Journal() { ::close(descriptor); }
  void append(const json &value) {
    const auto line = value.dump() + "\n";
    std::size_t offset = 0;
    while (offset < line.size()) {
      const auto count =
          ::write(descriptor, line.data() + offset, line.size() - offset);
      if (count < 0 && errno == EINTR)
        continue;
      posix_check(count > 0, "append witness");
      offset += static_cast<std::size_t>(count);
    }
    posix_check(::fsync(descriptor) == 0, "fsync independent witness");
  }
};
std::vector<char> payload(const std::string &run, std::size_t sequence,
                          std::size_t bytes) {
  const auto seed = "42193:" + run + ":" + std::to_string(sequence);
  std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)> context(
      EVP_MD_CTX_new(), EVP_MD_CTX_free);
  if (!context ||
      EVP_DigestInit_ex(context.get(), EVP_shake256(), nullptr) != 1 ||
      EVP_DigestUpdate(context.get(), seed.data(), seed.size()) != 1)
    throw std::runtime_error("SHAKE256 initialization");
  std::vector<char> data(bytes);
  if (EVP_DigestFinalXOF(context.get(),
                         reinterpret_cast<unsigned char *>(data.data()),
                         data.size()) != 1)
    throw std::runtime_error("SHAKE256 payload");
  return data;
}
std::string sha256(const std::vector<char> &data) {
  std::array<unsigned char, EVP_MAX_MD_SIZE> digest{};
  unsigned int size = 0;
  if (EVP_Digest(data.data(), data.size(), digest.data(), &size, EVP_sha256(),
                 nullptr) != 1)
    throw std::runtime_error("SHA256");
  std::ostringstream result;
  for (unsigned int i = 0; i < size; ++i)
    result << std::hex << std::setw(2) << std::setfill('0')
           << unsigned(digest[i]);
  return result.str();
}
struct Mismatch : std::runtime_error {
  using std::runtime_error::runtime_error;
};
void verify_body(ceph_mount_info *connection, const json &expected) {
  const auto started = Clock::now();
  File file(connection, expected.at("path").get<std::string>(), O_RDONLY);
  const auto count = expected.at("bytes").get<std::size_t>();
  std::vector<char> data(count);
  std::size_t offset = 0;
  while (offset < count) {
    auto got = checked(ceph_read(connection, file.get(), data.data() + offset,
                                 static_cast<int64_t>(count - offset),
                                 static_cast<int64_t>(offset)),
                       "read witnessed body");
    if (!got)
      throw Mismatch("Short successful read");
    offset += static_cast<std::size_t>(got);
  }
  char extra = 0;
  if (checked(ceph_read(connection, file.get(), &extra, 1,
                        static_cast<int64_t>(offset)),
              "read EOF") != 0 ||
      sha256(data) != expected.at("sha256"))
    throw Mismatch("Wrong successful body/length: " +
                   expected.at("path").get<std::string>());
  file.close();
  emit({{"event", "body_read"},
        {"path", expected.at("path")},
        {"bytes", count},
        {"duration_ms",
         std::chrono::duration<double, std::milli>(Clock::now() - started)
             .count()}});
}
json expected(const std::string &run, std::size_t sequence,
              const std::vector<char> &data) {
  return {{"run", run},
          {"sequence", sequence},
          {"path", "/objects/" + run + "/" + std::to_string(sequence)},
          {"bytes", data.size()},
          {"sha256", sha256(data)}};
}
json expected(const std::string &run, std::size_t sequence, std::size_t size) {
  return expected(run, sequence, payload(run, sequence, size));
}
void publish(ceph_mount_info *connection, Journal &journal,
             const fs::path &directory, const std::string &run,
             std::size_t sequence, std::size_t size) {
  const auto data = payload(run, sequence, size);
  auto record = expected(run, sequence, data);
  record["event"] = "attempt";
  record["utc_start_ms"] = epoch_ms();
  record["phase_start"] = contents(directory / "phase");
  journal.append(record);
  const auto start = Clock::now();
  try {
    const auto temporary = "/staging/" + run + "/" + std::to_string(sequence);
    File file(connection, temporary, O_WRONLY | O_CREAT | O_EXCL);
    std::size_t offset = 0;
    while (offset < data.size()) {
      auto wrote =
          checked(ceph_write(connection, file.get(), data.data() + offset,
                             static_cast<int64_t>(data.size() - offset),
                             static_cast<int64_t>(offset)),
                  "write staged file");
      if (!wrote)
        throw std::runtime_error("Zero-byte write");
      offset += static_cast<std::size_t>(wrote);
    }
    checked(ceph_fsync(connection, file.get(), 0), "fsync staged file");
    file.close();
    checked(ceph_rename(connection, temporary.c_str(),
                        record.at("path").get<std::string>().c_str()),
            "publish by rename");
    // The native implementation flushes data, mdlog/caps and unsafe requests.
    checked(ceph_sync_fs(connection), "flush publication metadata");
    record["event"] = "ack";
  } catch (const std::exception &error) {
    record["event"] = "ambiguous";
    record["error"] = error.what();
  }
  record["duration_ms"] =
      std::chrono::duration<double, std::milli>(Clock::now() - start).count();
  record["utc_end_ms"] = epoch_ms();
  record["phase_end"] = contents(directory / "phase");
  journal.append(record);
  emit(record);
}
std::map<std::size_t, json> acknowledged(const fs::path &file,
                                         bool attempts = false) {
  const auto raw = contents(file);
  if (!raw.empty() && raw.back() != '\n')
    throw std::runtime_error("Truncated witness history");
  std::ifstream input(file);
  std::string line;
  std::map<std::size_t, json> result;
  while (std::getline(input, line)) {
    if (input.eof())
      break;
    const auto value = json::parse(line);
    if (value.at("event") == (attempts ? "attempt" : "ack") &&
        !result.emplace(value.at("sequence").get<std::size_t>(), value).second)
      throw Mismatch("Duplicate witness sequence");
  }
  return result;
}
void resources() {
  rusage usage{};
  if (getrusage(RUSAGE_SELF, &usage) == 0)
    emit({{"event", "process_resources"},
          {"peak_rss_kib", usage.ru_maxrss},
          {"user_cpu_s", usage.ru_utime.tv_sec + usage.ru_utime.tv_usec / 1e6},
          {"system_cpu_s",
           usage.ru_stime.tv_sec + usage.ru_stime.tv_usec / 1e6}});
  emit({{"event", "resources"},
        {"proc_status", contents("/proc/self/status")},
        {"proc_io", contents("/proc/self/io")}});
}
int main(int argc, char **argv) {
  try {
    if (argc != 4)
      throw std::invalid_argument(
          "usage: cephfs-files seed|writer|reader|verify RUN_ID "
          "WITNESS_DIRECTORY");
    const std::string mode = argv[1], run = argv[2];
    if (run.size() != 32 ||
        run.find_first_not_of("0123456789abcdef") != std::string::npos)
      throw std::invalid_argument("Invalid run ID");
    if (mode != "seed" && mode != "writer" && mode != "reader" &&
        mode != "verify" && mode != "read-only-check")
      throw std::invalid_argument("Unknown mode");
    const fs::path directory = argv[3];
    std::signal(SIGINT, stop);
    std::signal(SIGTERM, stop);
    const auto size = number("PAYLOAD_BYTES", 262144, 16777216);
    const auto count = number("FILES", 128, 10000);
    if (size * count > 268435456)
      throw std::invalid_argument("Seed exceeds 256 MiB cap");
    const auto start = Clock::now();
    emit({{"event", "source"},
          {"sha256", WORKLOAD_SOURCE_SHA256},
          {"libcephfs_version", ceph_version(nullptr, nullptr, nullptr)}});
    Connection connection(mode == "seed" || mode == "writer" ? "lab-writer"
                                                             : "lab-reader");
    emit({{"event", "connected"},
          {"connect_ms",
           std::chrono::duration<double, std::milli>(Clock::now() - start)
               .count()}});
    if (mode == "seed" || mode == "writer") {
      if (!fs::create_directories(directory))
        throw std::runtime_error("Witness exists; never overwrite it");
      Journal journal(directory / "journal.jsonl");
      checked(ceph_mkdirs(connection.handle, ("/staging/" + run).c_str(), 0755),
              "make staging directory");
      checked(ceph_mkdirs(connection.handle, ("/objects/" + run).c_str(), 0755),
              "make publication directory");
      const auto end =
          Clock::now() + std::chrono::seconds(number("DURATION_S", 180, 900));
      std::size_t sequence = 0;
      do {
        publish(connection.handle, journal, directory, run, sequence++, size);
        if (mode == "writer")
          std::this_thread::sleep_for(std::chrono::milliseconds(100));
      } while (running && !fs::exists(directory / "stop") &&
               (mode == "seed" ? sequence < count : Clock::now() < end));
      if (acknowledged(directory / "journal.jsonl").empty() ||
          (mode == "seed" &&
           acknowledged(directory / "journal.jsonl").size() != count))
        throw std::runtime_error("No publication ACK");
    } else if (mode == "verify") {
      const auto acks = acknowledged(directory / "journal.jsonl"),
                 attempts = acknowledged(directory / "journal.jsonl", true);
      if (acks.empty())
        throw std::runtime_error("Missing ACK witness");
      for (const auto &[sequence, ack] : acks) {
        (void)ack;
        if (!attempts.count(sequence))
          throw Mismatch("ACK without a witnessed attempt");
      }
      std::size_t ambiguous_present = 0;
      for (const auto &[sequence, item] : attempts) {
        const auto bytes = item.at("bytes").get<std::size_t>();
        if (!bytes || bytes > 16777216 ||
            expected(run, sequence, bytes).at("sha256") != item.at("sha256"))
          throw Mismatch("Witness differs from deterministic payload corpus");
        if (acks.count(sequence)) {
          if (acks.at(sequence).at("path") != item.at("path") ||
              acks.at(sequence).at("sha256") != item.at("sha256") ||
              acks.at(sequence).at("bytes") != item.at("bytes"))
            throw Mismatch("ACK differs from attempt");
          verify_body(connection.handle, item);
        } else {
          struct ceph_statx st{};
          int status = ceph_statx(connection.handle,
                                  item.at("path").get<std::string>().c_str(),
                                  &st, CEPH_STATX_SIZE, AT_SYMLINK_NOFOLLOW);
          if (status == 0) {
            verify_body(connection.handle, item);
            ++ambiguous_present;
          } else
            checked(status == -ENOENT ? 0 : status, "reconcile ambiguous path");
        }
      }
      emit({{"event", "verified"},
            {"acks", acks.size()},
            {"attempts", attempts.size()},
            {"ambiguous_present", ambiguous_present},
            {"wrong", 0},
            {"missing_ack", 0}});
    } else if (mode == "read-only-check") {
      int fd = ceph_open(connection.handle, "/read-only-must-not-write",
                         O_WRONLY | O_CREAT | O_EXCL, 0600);
      if (fd >= 0) {
        ceph_close(connection.handle, fd);
        throw Mismatch("Read-only CephX client created a file");
      }
      if (fd != -EACCES && fd != -EPERM)
        checked(fd, "read-only negative control");
      emit({{"event", "read_only_check"},
            {"write_denied", true},
            {"errno", -fd}});
    } else {
      const auto end =
          Clock::now() + std::chrono::seconds(number("DURATION_S", 180, 900));
      std::size_t sequence = 0, verified = 0;
      while (running && Clock::now() < end &&
             !fs::exists(directory / "stop-reader")) {
        auto item = expected(run, sequence++ % count, size);
        const auto before = Clock::now();
        json record{{"event", "read"},
                    {"path", item["path"]},
                    {"utc_start_ms", epoch_ms()},
                    {"phase_start", contents(directory / "phase")}};
        try {
          verify_body(connection.handle, item);
          record["result"] = "ok";
          ++verified;
        } catch (const Mismatch &) {
          throw;
        } catch (const std::exception &error) {
          record["result"] = "fail";
          record["error"] = error.what();
        }
        record["duration_ms"] =
            std::chrono::duration<double, std::milli>(Clock::now() - before)
                .count();
        record["utc_end_ms"] = epoch_ms();
        emit(record);
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
      }
      if (!verified)
        throw std::runtime_error("No successful independent reads");
    }
    connection.close();
    resources();
  } catch (const Mismatch &error) {
    emit({{"event", "fatal"}, {"result", "mismatch"}, {"error", error.what()}});
    resources();
    return 2;
  } catch (const std::exception &error) {
    emit({{"event", "fatal"}, {"error", error.what()}});
    resources();
    return 1;
  }
}
