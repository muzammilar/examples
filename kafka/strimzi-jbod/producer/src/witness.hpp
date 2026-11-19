#pragma once

#include <fcntl.h>
#include <openssl/evp.h>
#include <unistd.h>
#include <zlib.h>

#include <cerrno>
#include <charconv>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <limits>
#include <nlohmann/json.hpp>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <utility>

namespace witness {
using Json = nlohmann::json;
namespace fs = std::filesystem;

inline std::runtime_error io_error(const std::string& action) {
  return std::runtime_error(action + ": " + std::strerror(errno));
}

class Descriptor {
 public:
  explicit Descriptor(int fd) : fd_(fd) {
    if (fd_ < 0) throw io_error("open witness");
  }
  Descriptor(const Descriptor&) = delete;
  Descriptor& operator=(const Descriptor&) = delete;
  ~Descriptor() {
    if (fd_ >= 0) ::close(fd_);
  }
  [[nodiscard]] int get() const { return fd_; }
  void sync() const {
    if (::fsync(fd_) != 0) throw io_error("fsync witness");
  }
  void close() {
    const int fd = std::exchange(fd_, -1);
    if (fd >= 0 && ::close(fd) != 0) throw io_error("close witness");
  }

 private:
  int fd_;
};

inline double now() {
  return std::chrono::duration<double>(
             std::chrono::system_clock::now().time_since_epoch())
      .count();
}

inline std::int64_t integer(std::string_view text, std::int64_t low,
                            std::int64_t high) {
  std::int64_t value = 0;
  const auto [end, error] =
      std::from_chars(text.data(), text.data() + text.size(), value);
  if (error != std::errc{} || end != text.data() + text.size() || value < low ||
      value > high)
    throw std::runtime_error("integer outside expected bounds: " +
                             std::string(text));
  return value;
}

inline std::string sha256(const std::string& body) {
  unsigned char digest[EVP_MAX_MD_SIZE];
  unsigned int size = 0;
  if (EVP_Digest(body.data(), body.size(), digest, &size, EVP_sha256(),
                 nullptr) != 1 ||
      size != 32)
    throw std::runtime_error("OpenSSL SHA-256 failed");
  constexpr char hex[] = "0123456789abcdef";
  std::string result(64, '0');
  for (unsigned int i = 0; i < size; ++i) {
    result[2 * i] = hex[digest[i] >> 4];
    result[2 * i + 1] = hex[digest[i] & 15];
  }
  return result;
}

inline void atomic_json(const fs::path& path, const Json& value) {
  const fs::path temporary = path.string() + ".tmp";
  Descriptor file(
      ::open(temporary.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600));
  const auto body = value.dump() + "\n";
  std::size_t written = 0;
  while (written < body.size()) {
    const auto count =
        ::write(file.get(), body.data() + written, body.size() - written);
    if (count < 0 && errno == EINTR) continue;
    if (count <= 0) throw io_error("write witness snapshot");
    written += static_cast<std::size_t>(count);
  }
  file.sync();
  file.close();
  fs::rename(temporary, path);
  Descriptor directory(
      ::open(path.parent_path().c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC));
  directory.sync();
}

// A polling-thread-owned gzip stream. Each sync persists a complete deflate
// prefix; finish persists the footer as well. Planned intents are synced before
// admission.
class Journal {
 public:
  explicit Journal(const fs::path& path)
      : file_(::open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC,
                     0600)) {
    const int duplicate = ::dup(file_.get());
    if (duplicate < 0) throw io_error("duplicate witness descriptor");
    gzip_ = ::gzdopen(duplicate, "wb1");
    if (gzip_ == nullptr) {
      ::close(duplicate);
      throw std::runtime_error("create gzip witness failed");
    }
    if (::gzbuffer(gzip_, 1024 * 1024) != 0) {
      ::gzclose(gzip_);
      gzip_ = nullptr;
      throw std::runtime_error("allocate gzip witness buffer failed");
    }
    try {
      Descriptor directory(::open(path.parent_path().c_str(),
                                  O_RDONLY | O_DIRECTORY | O_CLOEXEC));
      directory.sync();
    } catch (...) {
      ::gzclose(gzip_);
      gzip_ = nullptr;
      throw;
    }
  }
  Journal(const Journal&) = delete;
  Journal& operator=(const Journal&) = delete;
  ~Journal() {
    if (gzip_ != nullptr) ::gzclose(gzip_);
  }
  void append(const Json& event) {
    if (gzip_ == nullptr)
      throw std::runtime_error("append after witness close");
    const auto body = event.dump() + "\n";
    if (body.size() > std::numeric_limits<unsigned int>::max() ||
        ::gzwrite(gzip_, body.data(), static_cast<unsigned int>(body.size())) !=
            static_cast<int>(body.size()))
      throw std::runtime_error("gzip witness write failed");
  }
  void sync() {
    if (gzip_ == nullptr || ::gzflush(gzip_, Z_SYNC_FLUSH) != Z_OK)
      throw std::runtime_error("gzip witness sync failed");
    file_.sync();
  }
  void finish() {
    if (gzip_ == nullptr) return;
    const auto stream = std::exchange(gzip_, nullptr);
    if (::gzclose(stream) != Z_OK)
      throw std::runtime_error("gzip witness footer failed");
    file_.sync();
    file_.close();
  }

 private:
  Descriptor file_;
  gzFile gzip_ = nullptr;
};
}  // namespace witness
