#include <hdfs.h>
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
#include <random>
#include <sstream>
#include <stdexcept>
#include <string>
#include <system_error>
#include <thread>
#include <unistd.h>
#include <vector>

using json = nlohmann::json;
using Clock = std::chrono::steady_clock;
namespace fs = std::filesystem;
volatile std::sig_atomic_t running = 1;
void stop(int) { running = 0; }

std::string contents(const fs::path &path) {
  std::ifstream input(path);
  return {std::istreambuf_iterator<char>(input),
          std::istreambuf_iterator<char>()};
}
long long epoch_ms() {
  return std::chrono::duration_cast<std::chrono::milliseconds>(
             std::chrono::system_clock::now().time_since_epoch())
      .count();
}
void emit(json event) {
  event["utc_ms"] = epoch_ms();
  std::cout << event.dump() << std::endl;
}
void check(bool success, const std::string &operation) {
  if (!success)
    throw std::system_error(errno, std::generic_category(), operation);
}
std::size_t number(const char *key, std::size_t fallback, std::size_t maximum) {
  const char *value = std::getenv(key);
  if (!value)
    return fallback;
  std::size_t end = 0;
  auto result = std::stoull(value, &end);
  if (end != std::string(value).size() || result == 0 || result > maximum)
    throw std::invalid_argument(std::string("Invalid ") + key);
  return result;
}
class Digest {
  std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)> context{
      EVP_MD_CTX_new(), EVP_MD_CTX_free};

public:
  Digest() {
    if (!context ||
        EVP_DigestInit_ex(context.get(), EVP_sha256(), nullptr) != 1)
      throw std::runtime_error("SHA-256 initialization failed");
  }
  void update(const void *data, std::size_t count) {
    if (EVP_DigestUpdate(context.get(), data, count) != 1)
      throw std::runtime_error("SHA-256 update failed");
  }
  std::string finish() {
    std::array<unsigned char, EVP_MAX_MD_SIZE> bytes{};
    unsigned int count = 0;
    if (EVP_DigestFinal_ex(context.get(), bytes.data(), &count) != 1)
      throw std::runtime_error("SHA-256 finalization failed");
    std::ostringstream result;
    for (unsigned int i = 0; i < count; ++i)
      result << std::hex << std::setw(2) << std::setfill('0')
             << unsigned(bytes[i]);
    return result.str();
  }
};
class Connection {
public:
  hdfsFS handle = nullptr;
  Connection() {
    auto *builder = hdfsNewBuilder();
    if (!builder)
      throw std::runtime_error("No HDFS builder");
    hdfsBuilderSetNameNode(builder, "hdfs://lab");
    hdfsBuilderSetUserName(builder, "hadoop");
    hdfsBuilderSetForceNewInstance(builder);
    handle =
        hdfsBuilderConnect(builder); // consumes builder on success or failure
    check(handle != nullptr, "connect local HDFS nameservice");
  }
  Connection(const Connection &) = delete;
  Connection &operator=(const Connection &) = delete;
  ~Connection() {
    if (handle)
      hdfsDisconnect(handle);
  }
  void close() {
    auto value = handle;
    handle = nullptr;
    check(hdfsDisconnect(value) == 0, "disconnect HDFS");
  }
};
class File {
  hdfsFS filesystem;
  hdfsFile handle = nullptr;

public:
  File(hdfsFS connection, const std::string &path, int flags)
      : filesystem(connection) {
    auto *builder = hdfsStreamBuilderAlloc(filesystem, path.c_str(), flags);
    check(builder != nullptr, "allocate stream");
    if (flags == O_WRONLY &&
        (hdfsStreamBuilderSetReplication(builder, 3) != 0 ||
         hdfsStreamBuilderSetDefaultBlockSize(builder, 16777216) != 0)) {
      hdfsStreamBuilderFree(builder);
      throw std::runtime_error("Cannot set stream replication/block size");
    }
    handle = hdfsStreamBuilderBuild(builder); // consumes builder
    check(handle != nullptr, "open " + path);
  }
  File(const File &) = delete;
  File &operator=(const File &) = delete;
  ~File() {
    if (handle)
      hdfsCloseFile(filesystem, handle);
  }
  hdfsFile get() const { return handle; }
  void close() {
    auto value = handle;
    handle = nullptr; // native close frees the handle even on error
    check(hdfsCloseFile(filesystem, value) == 0, "close file");
  }
};
class Journal {
  int descriptor;

public:
  explicit Journal(const fs::path &path)
      : descriptor(::open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_APPEND,
                          0600)) {
    check(descriptor >= 0, "create independent witness");
  }
  Journal(const Journal &) = delete;
  Journal &operator=(const Journal &) = delete;
  ~Journal() { ::close(descriptor); }
  void append(const json &record) {
    auto line = record.dump() + "\n";
    std::size_t offset = 0;
    while (offset < line.size()) {
      auto count =
          ::write(descriptor, line.data() + offset, line.size() - offset);
      if (count < 0 && errno == EINTR)
        continue;
      check(count > 0, "append witness");
      offset += static_cast<std::size_t>(count);
    }
    check(::fsync(descriptor) == 0, "fsync witness");
  }
};
struct Mismatch : std::runtime_error {
  using std::runtime_error::runtime_error;
};
std::map<std::size_t, json> acknowledged(const fs::path &path,
                                         bool attempts = false) {
  std::map<std::size_t, json> result;
  std::ifstream input(path);
  std::string line;
  while (std::getline(input, line)) {
    if (input.eof())
      break; // writer may still be completing the last line
    auto item = json::parse(line);
    if (item["event"] == (attempts ? "attempt" : "ack")) {
      auto id = item.at("sequence").get<std::size_t>();
      if (!result.emplace(id, item).second)
        throw Mismatch("Duplicate witness ID");
    }
  }
  return result;
}
void verify_body(hdfsFS connection, const json &expected) {
  File file(connection, expected.at("path").get<std::string>(), O_RDONLY);
  Digest digest;
  std::array<char, 65536> block{};
  std::size_t bytes = 0;
  auto deadline = Clock::now() + std::chrono::seconds(60);
  for (;;) {
    if (Clock::now() > deadline)
      throw std::runtime_error("Read operation exhausted its retry budget");
    const auto count = hdfsRead(connection, file.get(), block.data(),
                                static_cast<tSize>(block.size()));
    if (count == -1 && errno == EINTR)
      continue;
    check(count >= 0, "read witnessed file");
    if (count == 0)
      break;
    digest.update(block.data(), static_cast<std::size_t>(count));
    bytes += static_cast<std::size_t>(count);
  }
  file.close();
  if (bytes != expected.at("bytes").get<std::size_t>() ||
      digest.finish() != expected.at("sha256").get<std::string>())
    throw Mismatch("Successful read returned wrong length/body: " +
                   expected.at("path").get<std::string>());
}
std::vector<char> payload(std::size_t size, std::size_t sequence) {
  std::mt19937_64 random(42193 + sequence);
  std::vector<char> data(size);
  for (std::size_t i = 0; i < size;) {
    auto word = random();
    for (unsigned int j = 0; j < 8 && i < size; ++j, ++i)
      data[i] = static_cast<char>((word >> (8 * j)) & 255);
  }
  return data;
}
std::string phase(const fs::path &directory) {
  auto value = contents(directory / "phase");
  return value.empty() ? "baseline" : value;
}
void publish(hdfsFS connection, Journal &journal, const std::string &run,
             const fs::path &directory, std::size_t sequence,
             std::size_t size) {
  auto data = payload(size, sequence);
  Digest digest;
  digest.update(data.data(), data.size());
  auto path = "/lab/objects/" + run + "/" + std::to_string(sequence);
  auto temporary = "/lab/staging/" + run + "/" + std::to_string(sequence);
  json record{{"event", "attempt"},
              {"sequence", sequence},
              {"path", path},
              {"bytes", size},
              {"sha256", digest.finish()},
              {"utc_start_ms", epoch_ms()},
              {"phase_start", phase(directory)}};
  journal.append(record); // externally witnessed before any remote mutation
  auto started = Clock::now();
  try {
    File file(connection, temporary, O_WRONLY);
    std::size_t offset = 0;
    while (offset < data.size()) {
      auto count = hdfsWrite(connection, file.get(), data.data() + offset,
                             static_cast<tSize>(std::min<std::size_t>(
                                 65536, data.size() - offset)));
      if (count < 0 && errno == EINTR)
        continue;
      check(count > 0, "write staged file");
      offset += static_cast<std::size_t>(count);
    }
    check(hdfsHSync(connection, file.get()) == 0, "hsync staged data");
    file.close();
    check(hdfsRename(connection, temporary.c_str(), path.c_str()) == 0,
          "publish closed file");
    record["event"] = "ack";
  } catch (const std::exception &error) {
    record["event"] = "ambiguous";
    record["error"] =
        error.what(); // a timeout/error never proves the mutation absent
  }
  record["duration_ms"] =
      std::chrono::duration<double, std::milli>(Clock::now() - started).count();
  record["utc_end_ms"] = epoch_ms();
  record["phase_end"] = phase(directory);
  journal.append(record);
  emit(record);
}
void resources() {
  json result{{"event", "resources"}};
  for (const auto *name : {"cpu.stat", "memory.peak", "io.stat"})
    result[name] = contents(fs::path("/sys/fs/cgroup") / name);
  emit(result);
}
int main(int argc, char **argv) {
  if (argc == 2 && std::string(argv[1]) == "--help") {
    std::cout << "files-workload seed|writer|reader|verify (RUN_ID required)\n";
    return 0;
  }
  try {
    if (argc != 2)
      throw std::invalid_argument("Expected one operation");
    const std::string mode = argv[1];
    if (mode != "seed" && mode != "writer" && mode != "reader" &&
        mode != "verify")
      throw std::invalid_argument("Unknown operation");
    const char *configured = std::getenv("RUN_ID");
    const std::string run = configured ? configured : "";
    if (run.size() != 32 ||
        run.find_first_not_of("0123456789abcdef") != std::string::npos)
      throw std::invalid_argument("RUN_ID must be 32 lowercase hex characters");
    const fs::path directory = fs::path("/witness") / run;
    const fs::path witness = directory / "journal.jsonl";
    std::signal(SIGTERM, stop);
    std::signal(SIGINT, stop);
    const auto start = Clock::now();
    emit({{"event", "source"}, {"source_sha256", WORKLOAD_SOURCE_SHA256}});
    Connection connection;
    emit({{"event", "connected"},
          {"connect_ms",
           std::chrono::duration<double, std::milli>(Clock::now() - start)
               .count()}});
    if (mode == "seed" || mode == "writer") {
      if (!fs::create_directory(directory))
        throw std::runtime_error("Witness run already exists; never overwrite");
      Journal journal(witness);
      for (const auto &path : {"/lab/staging/" + run, "/lab/objects/" + run})
        check(hdfsCreateDirectory(connection.handle, path.c_str()) == 0,
              "create owned directory");
      const auto duration = number("DURATION_S", 60, 180);
      const auto bytes = mode == "seed"
                             ? number("SEED_BYTES", 67108864, 268435456)
                             : number("PAYLOAD_BYTES", 1048576, 16777216);
      const auto deadline = Clock::now() + std::chrono::seconds(duration);
      std::size_t sequence = 0;
      do {
        publish(connection.handle, journal, run, directory, sequence++, bytes);
        if (mode == "seed")
          break;
        std::this_thread::sleep_for(std::chrono::milliseconds(200));
      } while (running && Clock::now() < deadline &&
               !fs::exists(directory / "stop"));
      auto acks = acknowledged(witness);
      if (acks.empty())
        throw std::runtime_error("No acknowledged publication");
      if (mode == "seed")
        verify_body(connection.handle, acks.at(0));
    } else if (mode == "reader") {
      const auto deadline =
          Clock::now() + std::chrono::seconds(number("DURATION_S", 60, 180));
      std::size_t cursor = 0, verified = 0;
      while (running && Clock::now() < deadline &&
             !fs::exists(directory / "stop-reader")) {
        auto acks = acknowledged(witness);
        if (acks.empty()) {
          std::this_thread::sleep_for(std::chrono::milliseconds(100));
          continue;
        }
        auto selected = acks.begin();
        std::advance(selected, cursor++ % acks.size());
        auto started = Clock::now();
        json record{{"event", "read"},
                    {"sequence", selected->first},
                    {"path", selected->second["path"]},
                    {"phase_start", phase(directory)},
                    {"utc_start_ms", epoch_ms()}};
        try {
          verify_body(connection.handle, selected->second);
          record["result"] = "ok";
          ++verified;
        } catch (const Mismatch &) {
          throw;
        } catch (const std::exception &error) {
          record["result"] = "fail";
          record["error"] = error.what();
        }
        record["duration_ms"] =
            std::chrono::duration<double, std::milli>(Clock::now() - started)
                .count();
        record["utc_end_ms"] = epoch_ms();
        record["phase_end"] = phase(directory);
        emit(record);
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
      }
      if (!verified)
        throw std::runtime_error("No successful live reads");
    } else {
      auto acks = acknowledged(witness);
      auto attempts = acknowledged(witness, true);
      if (acks.empty())
        throw std::runtime_error("No witnessed acknowledgements to verify");
      for (const auto &[sequence, ack] : acks) {
        if (!attempts.count(sequence))
          throw Mismatch("ACK has no prior attempt");
        for (const auto *field : {"path", "bytes", "sha256"})
          if (ack.at(field) != attempts.at(sequence).at(field))
            throw Mismatch("ACK differs from witnessed attempt");
      }
      std::size_t ambiguous_present = 0;
      for (const auto &[sequence, item] : attempts) {
        if (acks.count(sequence))
          verify_body(connection.handle, item);
        else if (hdfsExists(connection.handle,
                            item.at("path").get<std::string>().c_str()) == 0) {
          verify_body(connection.handle, item);
          ++ambiguous_present;
        } else if (errno != ENOENT)
          throw std::runtime_error(
              "Ambiguous final-path existence check failed");
      }
      emit({{"event", "verified"},
            {"attempts", attempts.size()},
            {"acks", acks.size()},
            {"ambiguous_published", ambiguous_present},
            {"wrong", 0},
            {"acknowledged_missing", 0}});
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
  return 0;
}
