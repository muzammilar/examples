#include <librdkafka/rdkafkacpp.h>

#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>

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
  try {
    const auto count = std::stol(env_or("COUNT", "2000000"));
    if (count < 1 || count > 5000000) throw std::out_of_range("COUNT");
    return static_cast<int>(count);
  } catch (const std::exception&) {
    throw std::runtime_error("COUNT must be an integer between 1 and 5000000");
  }
}
}  // namespace

int main() {
  try {
    const auto bootstrap = env_or("BOOTSTRAP_SERVERS", "kafka-broker-1:19092");
    const auto topic_name = env_or("TOPIC", "orders");
    const auto run_id = env_or("RUN_ID", "connect-demo");
    const int count = parse_count();
    const auto customer_count = std::stol(env_or("CUSTOMER_COUNT", "10000"));
    if (customer_count < 1) throw std::runtime_error("CUSTOMER_COUNT must be positive");
    std::string error;
    std::unique_ptr<RdKafka::Conf> config(RdKafka::Conf::create(RdKafka::Conf::CONF_GLOBAL));
    DeliveryReport reports;
    if (config->set("bootstrap.servers", bootstrap, error) != RdKafka::Conf::CONF_OK ||
        config->set("enable.idempotence", "true", error) != RdKafka::Conf::CONF_OK ||
        config->set("acks", "all", error) != RdKafka::Conf::CONF_OK ||
        config->set("dr_cb", &reports, error) != RdKafka::Conf::CONF_OK) {
      throw std::runtime_error("invalid librdkafka configuration: " + error);
    }
    std::unique_ptr<RdKafka::Producer> producer(RdKafka::Producer::create(config.get(), error));
    if (!producer) throw std::runtime_error("cannot create producer: " + error);
    std::unique_ptr<RdKafka::Topic> topic(RdKafka::Topic::create(producer.get(), topic_name, nullptr, error));
    if (!topic) throw std::runtime_error("cannot create topic handle: " + error);

    const auto now = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
    for (int i = 0; i < count; ++i) {
      kafka::demo::v1::Order order;
      const auto id = run_id + "-order-" + std::to_string(i);
      order.set_order_id(id);
      order.set_customer_id("customer-" + std::to_string(i % customer_count));
      order.set_amount_cents(1000 + i % 5000);
      order.set_created_at_unix_ms(now + i);
      std::string payload;
      if (!order.SerializeToString(&payload)) throw std::runtime_error("protobuf serialization failed");
      while (true) {
        const auto status = producer->produce(topic.get(), RdKafka::Topic::PARTITION_UA,
            RdKafka::Producer::RK_MSG_COPY, payload.data(), payload.size(), id.data(), id.size(), nullptr);
        if (status == RdKafka::ERR_NO_ERROR) break;
        if (status != RdKafka::ERR__QUEUE_FULL) throw std::runtime_error(RdKafka::err2str(status));
        producer->poll(100);
      }
      producer->poll(0);
    }
    const auto remaining = producer->flush(120000);
    if (remaining != 0 || reports.failed() || reports.delivered() != static_cast<std::uint64_t>(count))
      throw std::runtime_error("delivery did not complete for every order");
    std::cout << "delivered " << reports.delivered() << " protobuf orders to " << topic_name << '\n';
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return EXIT_FAILURE;
  }
  return EXIT_SUCCESS;
}
