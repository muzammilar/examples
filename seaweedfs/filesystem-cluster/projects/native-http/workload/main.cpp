#include "payload.h"
#include <chrono>
#include <cstdio>
#include <curl/curl.h>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <nlohmann/json.hpp>
#include <sys/resource.h>
#include <thread>
#include <unistd.h>

using json = nlohmann::json;
namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;
long long now_ms() {
  return std::chrono::duration_cast<std::chrono::milliseconds>(
             std::chrono::system_clock::now().time_since_epoch())
      .count();
}
void emit(json row) {
  row["utc_ms"] = now_ms();
  std::cout << row.dump() << std::endl;
}
std::size_t receive(char *data, std::size_t size, std::size_t count,
                    void *target) {
  const auto bytes = size * count;
  auto &body = *static_cast<std::vector<char> *>(target);
  if (bytes > 2097152 || body.size() > 2097152 - bytes)
    return 0;
  body.insert(body.end(), data, data + bytes);
  return bytes;
}
class HttpError : public std::runtime_error {
public:
  using std::runtime_error::runtime_error;
};
class Http {
  CURL *handle_;

public:
  Http() : handle_(curl_easy_init()) {
    if (!handle_)
      throw std::runtime_error("curl init");
  }
  ~Http() { curl_easy_cleanup(handle_); }
  Http(const Http &) = delete;
  Http &operator=(const Http &) = delete;
  std::vector<char> request(const std::string &endpoint,
                            const std::string &method, const std::string &path,
                            const std::vector<char> &input = {}) {
    if (endpoint != "http://filer0:8888" && endpoint != "http://filer1:8888")
      throw std::invalid_argument(
          "Only owned local filer endpoints are allowed");
    curl_easy_reset(handle_);
    const auto url = endpoint + path;
    std::vector<char> output;
    curl_easy_setopt(handle_, CURLOPT_URL, url.c_str());
    curl_easy_setopt(handle_, CURLOPT_PROXY, "");
    curl_easy_setopt(handle_, CURLOPT_PROTOCOLS_STR, "http");
    curl_easy_setopt(handle_, CURLOPT_FOLLOWLOCATION, 0L);
    curl_easy_setopt(handle_, CURLOPT_CONNECTTIMEOUT_MS, 2000L);
    curl_easy_setopt(handle_, CURLOPT_TIMEOUT_MS, 10000L);
    curl_easy_setopt(handle_, CURLOPT_NOSIGNAL, 1L);
    curl_easy_setopt(handle_, CURLOPT_WRITEFUNCTION, receive);
    curl_easy_setopt(handle_, CURLOPT_WRITEDATA, &output);
    curl_easy_setopt(handle_, CURLOPT_CUSTOMREQUEST, method.c_str());
    curl_slist *raw = nullptr;
    raw = curl_slist_append(raw, "Content-Type: application/octet-stream");
    raw = curl_slist_append(raw, "Expect:");
    std::unique_ptr<curl_slist, decltype(&curl_slist_free_all)> headers(
        raw, curl_slist_free_all);
    curl_easy_setopt(handle_, CURLOPT_HTTPHEADER, headers.get());
    if (method == "PUT") {
      curl_easy_setopt(handle_, CURLOPT_POSTFIELDS, input.data());
      curl_easy_setopt(handle_, CURLOPT_POSTFIELDSIZE_LARGE,
                       static_cast<curl_off_t>(input.size()));
    }
    const auto result = curl_easy_perform(handle_);
    long status = 0;
    curl_easy_getinfo(handle_, CURLINFO_RESPONSE_CODE, &status);
    if (result != CURLE_OK)
      throw HttpError(std::string("HTTP transport: ") +
                      curl_easy_strerror(result));
    if (status < 200 || status >= 300)
      throw HttpError("HTTP status " + std::to_string(status));
    return output;
  }
};
void append(FILE *file, const json &row) {
  auto text = row.dump() + "\n";
  if (std::fwrite(text.data(), 1, text.size(), file) != text.size() ||
      std::fflush(file) || ::fsync(::fileno(file)))
    throw std::runtime_error("Cannot persist external witness");
}
fs::path journal(const std::string &run) {
  return fs::path("/results/native-http/journals") / (run + ".jsonl");
}
std::map<std::size_t, json> acknowledged(const std::string &run) {
  std::ifstream input(journal(run));
  if (!input)
    throw std::runtime_error("Missing witness");
  std::map<std::size_t, json> result;
  std::string line;
  while (std::getline(input, line)) {
    if (input.eof())
      throw std::runtime_error("Truncated witness");
    auto row = json::parse(line);
    if (row.at("run") != run)
      throw std::runtime_error("Foreign witness");
    if (row.at("event") == "ack" &&
        !result.emplace(row.at("sequence").get<std::size_t>(), row).second)
      throw std::runtime_error("Duplicate ACK");
  }
  return result;
}
void publish(Http &http, const std::string &run, std::size_t count,
             std::size_t size, bool live) {
  if (!count || count > 1024 || !size || size > 1048576 ||
      count * size > 268435456)
    throw std::invalid_argument("Corpus bounds");
  fs::create_directories(journal(run).parent_path());
  const auto close_file = [](FILE *file) { std::fclose(file); };
  std::unique_ptr<FILE, decltype(close_file)> witness(
      std::fopen(journal(run).c_str(), "wx"), close_file);
  if (!witness)
    throw std::runtime_error("Witness exists or cannot be created");
  for (std::size_t seq = 0; seq < count; ++seq) {
    if (live &&
        fs::exists(fs::path("/results/native-http") / ("stop-writer-" + run)))
      break;
    auto body = payload(run, seq, size);
    json row{{"event", "attempt"},     {"run", run},
             {"sequence", seq},        {"bytes", size},
             {"sha256", sha256(body)}, {"utc_start_ms", now_ms()}};
    append(witness.get(), row);
    auto started = Clock::now();
    try {
      auto response = http.request(
          "http://filer0:8888", "PUT",
          "/native-http/" + run + "/" + std::to_string(seq) +
              "?replication=200&collection=filesystem-lab&fsync=true",
          body);
      auto receipt = json::parse(response.begin(), response.end());
      if (receipt.contains("error") ||
          receipt.at("size").get<std::size_t>() != size)
        throw std::runtime_error("Invalid filer publication receipt");
      row["event"] = "ack";
    } catch (const std::exception &error) {
      row["event"] = "ambiguous";
      row["error"] = error.what();
    }
    row["duration_ms"] =
        std::chrono::duration<double, std::milli>(Clock::now() - started)
            .count();
    row["utc_end_ms"] = now_ms();
    append(witness.get(), row);
    emit(row);
    if (!live && row["event"] != "ack")
      throw std::runtime_error("Ambiguous seed; preserve data");
    if (live)
      std::this_thread::sleep_for(std::chrono::milliseconds(100));
  }
}
void verify(Http &http, const std::string &run, const std::string &endpoint) {
  const auto acks = acknowledged(run);
  if (acks.empty())
    throw std::runtime_error("Empty ACK set");
  for (const auto &[seq, row] : acks) {
    const auto size = row.at("bytes").get<std::size_t>();
    if (!size || size > 1048576 ||
        row.at("sha256") != sha256(payload(run, seq, size)))
      throw std::runtime_error("Witness differs from deterministic oracle");
    const auto started = Clock::now();
    auto body = http.request(endpoint, "GET",
                             "/native-http/" + run + "/" + std::to_string(seq));
    if (body.size() != size || sha256(body) != row.at("sha256"))
      throw std::runtime_error("Wrong successful body");
    emit({{"event", "read"},
          {"run", run},
          {"sequence", seq},
          {"duration_ms",
           std::chrono::duration<double, std::milli>(Clock::now() - started)
               .count()}});
  }
  emit({{"event", "verified"},
        {"acks", acks.size()},
        {"wrong", 0},
        {"missing_ack", 0}});
}
int main(int argc, char **argv) {
  try {
    if (argc < 3)
      throw std::invalid_argument("mode run [count bytes|endpoint]");
    const std::string mode = argv[1], run = argv[2];
    if (run.size() != 32 ||
        run.find_first_not_of("0123456789abcdef") != std::string::npos)
      throw std::invalid_argument("Run identity");
    if (curl_global_init(CURL_GLOBAL_DEFAULT) != CURLE_OK)
      throw std::runtime_error("curl global init");
    emit({{"event", "source"},
          {"sha256", HTTP_SOURCE_SHA256},
          {"curl", curl_version()}});
    {
      Http http;
      if ((mode == "seed" || mode == "writer") && argc == 5)
        publish(http, run, std::stoull(argv[3]), std::stoull(argv[4]),
                mode == "writer");
      else if (mode == "verify" && argc == 4)
        verify(http, run, argv[3]);
      else if (mode == "reader" && argc == 4) {
        const std::string stop_run = argv[3];
        if (stop_run.size() != 32 ||
            stop_run.find_first_not_of("0123456789abcdef") != std::string::npos)
          throw std::invalid_argument("Stop identity");
        while (!fs::exists(fs::path("/results/native-http") /
                           ("stop-reader-" + stop_run))) {
          try {
            verify(http, run, "http://filer1:8888");
            emit({{"event", "read_cycle"}, {"run", run}});
          } catch (const HttpError &error) {
            emit({{"event", "read_error"}, {"error", error.what()}});
          }
          std::this_thread::sleep_for(std::chrono::milliseconds(200));
        }
      } else
        throw std::invalid_argument("Mode/arguments");
    }
    curl_global_cleanup();
    rusage usage{};
    if (getrusage(RUSAGE_SELF, &usage))
      throw std::runtime_error("rusage");
    emit({{"event", "resources"},
          {"peak_rss_kib", usage.ru_maxrss},
          {"user_cpu_s", usage.ru_utime.tv_sec + usage.ru_utime.tv_usec / 1e6},
          {"system_cpu_s",
           usage.ru_stime.tv_sec + usage.ru_stime.tv_usec / 1e6}});
  } catch (const std::exception &error) {
    emit({{"event", "fatal"}, {"error", error.what()}});
    return 1;
  }
}
