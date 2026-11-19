#include <librdkafka/rdkafkacpp.h>

#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <memory>
#include <thread>
#include <unordered_map>
#include <vector>

#include "order.pb.h"
#include "witness.hpp"

namespace {
using witness::Json;
using Clock = std::chrono::steady_clock;
namespace fs = std::filesystem;

struct Counts {
  std::uint64_t intended = 0, attempted = 0, enqueued = 0, acknowledged = 0;
  std::uint64_t ambiguous = 0, rejected = 0, admission_retries = 0,
                payload_bytes = 0;
  double last_ack = 0;
};

struct Failure {
  std::string reason;
  void set(const std::string& value) {
    if (reason.empty()) reason = value;
  }
  void check() const {
    if (!reason.empty()) throw std::runtime_error(reason);
  }
};

struct Pending {
  std::uint64_t sequence;
  std::int64_t created_ms;
  std::string id, payload, digest;
  Clock::time_point started;
  double first_send = 0;
  std::uint64_t admission_attempts = 0;
};

Json body(const Pending& value) {
  return {{"seq", value.sequence},
          {"id", value.id},
          {"created_ms", value.created_ms},
          {"sha256", value.digest},
          {"bytes", value.payload.size()}};
}

// Delivery/error/statistics callbacks below are served by this application's
// sole polling thread. No other application thread calls poll or flush.
class Deliveries final : public RdKafka::DeliveryReportCb {
 public:
  Deliveries(witness::Journal& journal, Counts& counts, Failure& failure)
      : journal_(journal), counts_(counts), failure_(failure) {}

  Pending* add(std::unique_ptr<Pending> value) {
    auto* key = value.get();
    if (!pending_.emplace(key, std::move(value)).second)
      throw std::runtime_error("reused pending delivery token");
    return key;
  }

  void rejected(Pending* token, RdKafka::ErrorCode error) {
    auto position = pending_.find(token);
    if (position == pending_.end())
      throw std::runtime_error("unknown rejected token");
    record(*position->second, "rejected", -1, -1, RdKafka::err2str(error));
    ++counts_.rejected;
    pending_.erase(position);
  }

  void dr_cb(RdKafka::Message& message) override {
    try {
      auto* token = static_cast<Pending*>(message.msg_opaque());
      auto position = pending_.find(token);
      if (position == pending_.end())
        throw std::runtime_error("unknown delivery token");
      const auto& value = *position->second;
      if (message.err() == RdKafka::ERR_NO_ERROR) {
        if (message.partition() < 0 ||
            message.status() != RdKafka::Message::MSG_STATUS_PERSISTED ||
            message.key() == nullptr || *message.key() != value.id ||
            message.len() != value.payload.size())
          throw std::runtime_error(
              "invalid successful delivery metadata: " +
              Json({{"partition", message.partition()},
                    {"offset", message.offset()},
                    {"status", message.status()},
                    {"bytes", message.len()},
                    {"key", message.key() ? *message.key() : "<missing>"}})
                  .dump());
        record(value, "ack", message.partition(), message.offset(), "",
               static_cast<int>(message.status()));
        ++counts_.acknowledged;
        counts_.last_ack = witness::now();
      } else {
        // A failed delivery report need not mean the broker never stored it.
        record(value, "ambiguous", message.partition(), message.offset(),
               message.errstr(), static_cast<int>(message.status()));
        ++counts_.ambiguous;
      }
      pending_.erase(position);
    } catch (const std::exception& error) {
      failure_.set(error.what());
    }
  }

  [[nodiscard]] bool empty() const { return pending_.empty(); }

 private:
  void record(const Pending& value, const char* stage, int partition,
              std::int64_t offset, const std::string& error,
              int persistence_status = -1) {
    auto event = body(value);
    event.update(
        {{"stage", stage},
         {"time", witness::now()},
         {"first_send_time", value.first_send},
         {"seconds",
          std::chrono::duration<double>(Clock::now() - value.started).count()},
         {"admission_attempts", value.admission_attempts},
         {"partition", partition},
         {"offset", offset >= 0 ? Json(offset) : Json(nullptr)},
         {"offset_known", offset >= 0},
         {"native_offset", offset},
         {"persistence_status", persistence_status}});
    if (!error.empty()) event["error"] = error;
    journal_.append(event);
  }

  witness::Journal& journal_;
  Counts& counts_;
  Failure& failure_;
  std::unordered_map<Pending*, std::unique_ptr<Pending>> pending_;
};

class NativeEvents final : public RdKafka::EventCb {
 public:
  NativeEvents(witness::Journal& journal, Failure& failure)
      : journal_(journal), failure_(failure) {}

  void event_cb(RdKafka::Event& event) override {
    try {
      if (event.type() == RdKafka::Event::EVENT_STATS) {
        const auto data = Json::parse(event.str());
        journal_.append(
            {{"time", witness::now()}, {"kind", "statistics"}, {"data", data}});
        journal_.sync();
        last_statistics = data.at("time").get<double>();
      } else if (event.type() == RdKafka::Event::EVENT_ERROR) {
        journal_.append({{"time", witness::now()},
                         {"kind", "error"},
                         {"code", static_cast<int>(event.err())},
                         {"reason", event.str()},
                         {"fatal", event.fatal()}});
        journal_.sync();
        if (event.fatal())
          failure_.set("fatal native producer error: " + event.str());
      }
    } catch (const std::exception& error) {
      failure_.set(error.what());
    }
  }

  double last_statistics = 0;

 private:
  witness::Journal& journal_;
  Failure& failure_;
};

Json progress(const Counts& counts, std::uint64_t minimum,
              const std::string& run_id, const std::string& topic,
              bool requested) {
  return {{"time", witness::now()},
          {"run_id", run_id},
          {"topic", topic},
          {"minimum_messages", minimum},
          {"intended", counts.intended},
          {"attempted", counts.attempted},
          {"enqueued", counts.enqueued},
          {"acknowledged", counts.acknowledged},
          {"ambiguous", counts.ambiguous},
          {"rejected", counts.rejected},
          {"admission_retries", counts.admission_retries},
          {"serialized_payload_bytes", counts.payload_bytes},
          {"last_ack_time", counts.last_ack},
          {"finish_requested", requested},
          {"application_mutation_retries", 0},
          {"native_version", RdKafka::version_str()}};
}

std::uint64_t bounded(const Json& plan, const char* name, std::uint64_t low,
                      std::uint64_t high) {
  const auto& value = plan.at(name);
  if (!value.is_number_integer())
    throw std::runtime_error(std::string("invalid ") + name);
  return static_cast<std::uint64_t>(
      witness::integer(value.dump(), static_cast<std::int64_t>(low),
                       static_cast<std::int64_t>(high)));
}

void run() {
  const char* configured = std::getenv("WITNESS_DIR");
  const fs::path root(configured == nullptr ? "/witness" : configured);
  std::ifstream input(root / "case.json");
  if (!input) throw std::runtime_error("cannot open example-owned case plan");
  const auto plan = Json::parse(input);
  bounded(plan, "format_version", 1, 1);
  const auto minimum = bounded(plan, "minimum_messages", 1, 2000000);
  const auto warmup = bounded(plan, "warmup_messages", 1, minimum);
  const auto live_rate = bounded(plan, "live_rate", 1, 100000);
  const auto burst_rate = bounded(plan, "burst_rate", 0, 1000000);
  const auto duration = bounded(plan, "duration_seconds", 60, 7200);
  const auto delivery_timeout =
      bounded(plan, "delivery_timeout_ms", 1000, 600000);
  const auto run_id = plan.at("run_id").get<std::string>();
  const auto topic_name = plan.at("topic").get<std::string>();
  const auto bootstrap = plan.at("bootstrap_servers").get<std::string>();
  if (run_id.empty() || topic_name.empty() || bootstrap.empty())
    throw std::runtime_error("empty Kafka case identity");
  if (RdKafka::version_str() != "2.12.1")
    throw std::runtime_error("unexpected native producer version: " +
                             RdKafka::version_str());

  witness::Journal journal(root / "producer.jsonl.gz");
  witness::Journal native(root / "producer-native.jsonl.gz");
  Counts counts;
  Failure failure;
  Deliveries deliveries(journal, counts, failure);
  NativeEvents events(native, failure);
  std::unique_ptr<RdKafka::Conf> config(
      RdKafka::Conf::create(RdKafka::Conf::CONF_GLOBAL));
  std::string error;
  for (const auto& [key, value] :
       std::vector<std::pair<std::string, std::string>>{
           {"bootstrap.servers", bootstrap},
           {"client.id", "producer-" + run_id},
           {"enable.idempotence", "true"},
           {"acks", "all"},
           {"allow.auto.create.topics", "false"},
           {"linger.ms", "5"},
           {"compression.type", "lz4"},
           {"statistics.interval.ms", "1000"},
           {"delivery.timeout.ms", std::to_string(delivery_timeout)},
           {"request.timeout.ms", "5000"},
           {"socket.timeout.ms", "10000"},
           {"queue.buffering.max.messages", "100000"},
           {"queue.buffering.max.kbytes", "65536"},
           {"delivery.report.only.error", "false"}}) {
    if (config->set(key, value, error) != RdKafka::Conf::CONF_OK)
      throw std::runtime_error("invalid producer config " + key + ": " + error);
  }
  if (config->set("dr_cb", &deliveries, error) != RdKafka::Conf::CONF_OK ||
      config->set("event_cb", &events, error) != RdKafka::Conf::CONF_OK)
    throw std::runtime_error("cannot configure producer callbacks: " + error);
  std::unique_ptr<RdKafka::Producer> producer(
      RdKafka::Producer::create(config.get(), error));
  if (!producer) throw std::runtime_error("cannot create producer: " + error);
  std::unique_ptr<RdKafka::Topic> topic(
      RdKafka::Topic::create(producer.get(), topic_name, nullptr, error));
  if (!topic) throw std::runtime_error("cannot create topic: " + error);

  const auto deadline = Clock::now() + std::chrono::seconds(duration);
  auto next_send = Clock::now();
  std::uint64_t previous_rate = 0;
  bool requested = false, aborted = false;
  while (true) {
    requested = fs::exists(root / "finish-producer");
    if (fs::exists(root / "abort-producer")) {
      aborted = true;
      break;
    }
    if (requested && counts.acknowledged >= minimum) break;
    if (Clock::now() >= deadline)
      throw std::runtime_error("producer safety deadline exhausted");
    if (counts.intended >= minimum * 4)
      throw std::runtime_error(
          "producer exceeded four times minimum without recovery");

    std::uint64_t batch = 1024;
    if (counts.intended < warmup)
      batch = std::min(batch, warmup - counts.intended);
    else if (counts.intended < minimum)
      batch = std::min(batch, minimum - counts.intended);
    std::vector<Pending*> prepared;
    for (std::uint64_t i = 0; i < batch; ++i) {
      const auto sequence = counts.intended;
      auto value = std::make_unique<Pending>();
      value->sequence = sequence;
      value->created_ms = static_cast<std::int64_t>(witness::now() * 1000);
      value->id = run_id + "-order-" + std::to_string(sequence);
      kafka::demo::v1::Order order;
      order.set_order_id(value->id);
      order.set_customer_id("customer-" + std::to_string(sequence % 10));
      order.set_amount_cents(1000 + static_cast<std::int64_t>(sequence % 5000));
      order.set_created_at_unix_ms(value->created_ms);
      if (!order.SerializeToString(&value->payload))
        throw std::runtime_error("protobuf serialization failed");
      value->digest = witness::sha256(value->payload);
      auto intent = body(*value);
      intent.update({{"stage", "intent"}, {"time", witness::now()}});
      journal.append(intent);
      counts.payload_bytes += value->payload.size();
      prepared.push_back(deliveries.add(std::move(value)));
      ++counts.intended;
    }
    journal
        .sync();  // The entire batch's intents precede every possible mutation.

    for (auto* value : prepared) {
      const auto rate =
          requested || value->sequence < warmup ? burst_rate : live_rate;
      if (rate != previous_rate) {
        next_send = Clock::now();
        previous_rate = rate;
      }
      if (rate != 0) {
        next_send = std::max(next_send, Clock::now());
        next_send += std::chrono::duration_cast<Clock::duration>(
            std::chrono::duration<double>(1.0 / static_cast<double>(rate)));
        producer->poll(0);
        std::this_thread::sleep_until(next_send);
      }
      failure.check();
      if (Clock::now() >= deadline)
        throw std::runtime_error("producer deadline during admission");
      value->started = Clock::now();
      value->first_send = witness::now();
      ++counts.attempted;
      while (true) {
        ++value->admission_attempts;
        const auto status = producer->produce(
            topic.get(), RdKafka::Topic::PARTITION_UA,
            RdKafka::Producer::RK_MSG_COPY, value->payload.data(),
            value->payload.size(), value->id.data(), value->id.size(), value);
        if (status == RdKafka::ERR_NO_ERROR) {
          ++counts.enqueued;
          break;
        }
        if (status != RdKafka::ERR__QUEUE_FULL) {
          deliveries.rejected(value, status);
          break;
        }
        ++counts.admission_retries;
        producer->poll(10);
        failure.check();
        if (Clock::now() >= deadline)
          throw std::runtime_error("producer deadline while queue full");
      }
      producer->poll(0);
      failure.check();
      // A callback may have deleted value; never dereference it after polling.
    }
    journal.sync();
    witness::atomic_json(
        root / "producer-progress.json",
        progress(counts, minimum, run_id, topic_name, requested));
  }
  if (producer->flush(120000) != RdKafka::ERR_NO_ERROR)
    throw std::runtime_error("native producer flush timed out");
  failure.check();
  if (!deliveries.empty() ||
      counts.enqueued != counts.acknowledged + counts.ambiguous ||
      counts.attempted != counts.enqueued + counts.rejected ||
      counts.intended != counts.attempted)
    throw std::runtime_error("producer outcome accounting differs");
  const auto statistics_deadline = Clock::now() + std::chrono::seconds(5);
  while (events.last_statistics < counts.last_ack) {
    if (Clock::now() >= statistics_deadline)
      throw std::runtime_error(
          "no native statistics after final acknowledgement");
    producer->poll(100);
    failure.check();
  }
  journal.finish();
  native.finish();
  auto result = progress(counts, minimum, run_id, topic_name, requested);
  result["stopped_by_controller"] =
      !aborted && requested && counts.attempted >= minimum;
  result["aborted"] = aborted;
  result["final_statistics_time"] = events.last_statistics;
  witness::atomic_json(root / "producer-progress.json", result);
  witness::atomic_json(root / "producer.json", result);
  std::cout << result.dump() << std::endl;
  if (aborted)
    throw std::runtime_error(
        "controller aborted this case; complete witness retained");
}
}  // namespace

int main(int argc, char** argv) {
  if (argc == 2 && std::string_view(argv[1]) == "--version") {
    std::cout << RdKafka::version_str() << std::endl;
    return EXIT_SUCCESS;
  }
  if (argc != 1) {
    std::cerr << "usage: order-producer [--version]\n";
    return EXIT_FAILURE;
  }
  try {
    run();
    return EXIT_SUCCESS;
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return EXIT_FAILURE;
  }
}
