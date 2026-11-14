#include <librdkafka/rdkafkacpp.h>

#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>

#include "order.pb.h"

namespace {

std::string env_or(const char* name, const char* fallback) {
  const char* value = std::getenv(name);
  return value == nullptr || *value == '\0' ? fallback : value;
}

class DeliveryReport final : public RdKafka::DeliveryReportCb {
 public:
  void dr_cb(RdKafka::Message& message) override {
    if (message.err() != RdKafka::ERR_NO_ERROR) {
      failed_.store(true);
      std::cerr << "delivery failed: " << message.errstr() << '\n';
      return;
    }
    delivered_.fetch_add(1);
  }

  [[nodiscard]] std::uint64_t delivered() const { return delivered_.load(); }
  [[nodiscard]] bool failed() const { return failed_.load(); }

 private:
  std::atomic<std::uint64_t> delivered_{0};
  std::atomic<bool> failed_{false};
};

int parse_count() {
  const auto raw = env_or("COUNT", "1000000");
  try {
    const auto count = std::stol(raw);
    if (count < 1 || count > 2000000) {
      throw std::out_of_range("COUNT");
    }
    return static_cast<int>(count);
  } catch (const std::exception&) {
    throw std::runtime_error("COUNT must be an integer between 1 and 2000000");
  }
}

}  // namespace

int main() {
  try {
    const auto bootstrap = env_or(
        "BOOTSTRAP_SERVERS", "kafka-kafka-bootstrap.kafka.svc:9092");
    const auto topic_name = env_or("TOPIC", "orders");
    const auto run_id = env_or("RUN_ID", "strimzi-demo");
    const int count = parse_count();
    const auto chaos_after = std::stol(env_or("CHAOS_AFTER_RECORDS", "0"));
    const auto chaos_pause_seconds = std::stol(env_or("CHAOS_PAUSE_SECONDS", "0"));
    if (chaos_after < 0 || chaos_after >= count ||
        (chaos_after > 0 && chaos_pause_seconds < 1)) {
      throw std::runtime_error(
          "CHAOS_AFTER_RECORDS must be 0 or less than COUNT, and a chaos pause must be positive");
    }

    std::string error;
    std::unique_ptr<RdKafka::Conf> config(
        RdKafka::Conf::create(RdKafka::Conf::CONF_GLOBAL));
    DeliveryReport delivery_report;
    if (config->set("bootstrap.servers", bootstrap, error) !=
            RdKafka::Conf::CONF_OK ||
        config->set("enable.idempotence", "true", error) !=
            RdKafka::Conf::CONF_OK ||
        config->set("acks", "all", error) != RdKafka::Conf::CONF_OK ||
        config->set("linger.ms", "5", error) != RdKafka::Conf::CONF_OK ||
        config->set("compression.type", "lz4", error) !=
            RdKafka::Conf::CONF_OK ||
        config->set("delivery.report.only.error", "false", error) !=
            RdKafka::Conf::CONF_OK ||
        config->set("dr_cb", &delivery_report, error) !=
            RdKafka::Conf::CONF_OK) {
      throw std::runtime_error("invalid librdkafka configuration: " + error);
    }

    std::unique_ptr<RdKafka::Producer> producer(
        RdKafka::Producer::create(config.get(), error));
    if (!producer) {
      throw std::runtime_error("cannot create producer: " + error);
    }
    std::unique_ptr<RdKafka::Topic> topic(
        RdKafka::Topic::create(producer.get(), topic_name, nullptr, error));
    if (!topic) {
      throw std::runtime_error("cannot create topic handle: " + error);
    }

    const auto now = std::chrono::duration_cast<std::chrono::milliseconds>(
                         std::chrono::system_clock::now().time_since_epoch())
                         .count();
    for (int i = 0; i < count; ++i) {
      kafka::demo::v1::Order order;
      const auto id = run_id + "-order-" + std::to_string(i);
      order.set_order_id(id);
      order.set_customer_id("customer-" + std::to_string(i % 10));
      order.set_amount_cents(1000 + (i % 5000));
      order.set_created_at_unix_ms(now + i);

      std::string payload;
      if (!order.SerializeToString(&payload)) {
        throw std::runtime_error("protobuf serialization failed for " + id);
      }

      while (true) {
        const auto status = producer->produce(
            topic.get(), RdKafka::Topic::PARTITION_UA,
            RdKafka::Producer::RK_MSG_COPY, payload.data(), payload.size(),
            id.data(), id.size(), nullptr);
        if (status == RdKafka::ERR_NO_ERROR) {
          break;
        }
        if (status != RdKafka::ERR__QUEUE_FULL) {
          throw std::runtime_error(std::string("produce failed: ") +
                                   RdKafka::err2str(status));
        }
        producer->poll(100);
      }
      producer->poll(0);

      if (chaos_after > 0 && i + 1 == chaos_after) {
        const auto remaining = producer->flush(120000);
        if (remaining != 0 || delivery_report.failed() ||
            delivery_report.delivered() != static_cast<std::uint64_t>(chaos_after)) {
          throw std::runtime_error("producer failed to flush before the chaos checkpoint");
        }
        std::cout << "CHAOS_CHECKPOINT delivered " << delivery_report.delivered()
                  << " records; pausing " << chaos_pause_seconds << " seconds"
                  << std::endl;
        std::this_thread::sleep_for(
            std::chrono::seconds(chaos_pause_seconds));
      }
    }

    const auto remaining = producer->flush(120000);
    if (remaining != 0 || delivery_report.failed() ||
        delivery_report.delivered() != static_cast<std::uint64_t>(count)) {
      throw std::runtime_error("flush/delivery failed: expected " +
                               std::to_string(count) + ", delivered " +
                               std::to_string(delivery_report.delivered()) +
                               ", outstanding " + std::to_string(remaining));
    }
    std::cout << "delivered " << delivery_report.delivered()
              << " protobuf orders to " << topic_name << '\n';
    return EXIT_SUCCESS;
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return EXIT_FAILURE;
  }
}
