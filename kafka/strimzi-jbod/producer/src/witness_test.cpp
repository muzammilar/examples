#include "witness.hpp"

#include <iostream>

namespace {
void require(bool condition, const char* message) {
  if (!condition) throw std::runtime_error(message);
}
template <class F>
void rejected(F operation) {
  bool failed = false;
  try {
    operation();
  } catch (const std::exception&) {
    failed = true;
  }
  require(failed, "invalid input was accepted");
}
}  // namespace
int main() {
  try {
    require(
        witness::sha256("abc") ==
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        "SHA-256 differs");
    require(witness::integer("1000000", 1, 2000000) == 1000000,
            "integer differs");
    for (const auto* value : {"", "1x", "-1", "2000001"})
      rejected([&] { witness::integer(value, 1, 2000000); });
    std::string pattern = "/tmp/kafka-witness-XXXXXX";
    const char* created = ::mkdtemp(pattern.data());
    if (created == nullptr) throw witness::io_error("temporary test directory");
    const witness::fs::path root(created);
    const auto path = root / "witness.jsonl.gz";
    {
      witness::Journal journal(path);
      journal.append({{"stage", "intent"}, {"seq", 1}});
      journal.sync();
      journal.append({{"stage", "ack"}, {"seq", 1}, {"offset", 4}});
      journal.finish();
    }
    rejected([&] { witness::Journal duplicate(path); });
    gzFile reader = ::gzopen(path.c_str(), "rb");
    require(reader != nullptr, "cannot read witness");
    char buffer[1024];
    const int count = ::gzread(reader, buffer, sizeof(buffer));
    require(count > 0, "empty witness");
    const std::string contents(buffer, static_cast<std::size_t>(count));
    require(::gzclose(reader) == Z_OK, "invalid gzip footer");
    const auto split = contents.find('\n');
    require(split != std::string::npos, "missing intent");
    require(
        witness::Json::parse(contents.substr(0, split))["stage"] == "intent",
        "intent differs");
    require(witness::Json::parse(contents.substr(split + 1))["offset"] == 4,
            "ack differs");
    witness::atomic_json(root / "progress.json", {{"acknowledged", 1}});
    witness::atomic_json(root / "progress.json", {{"acknowledged", 2}});
    std::ifstream file(root / "progress.json");
    require(witness::Json::parse(file)["acknowledged"] == 2,
            "atomic replacement differs");
    witness::fs::remove_all(root);
    std::cout << "exclusive creation, durable gzip, atomic progress, SHA-256 "
                 "and strict bounds passed\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
