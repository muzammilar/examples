use std::{
    env,
    error::Error,
    thread,
    time::{Duration, Instant},
};

use prost::Message;
use rdkafka::{
    ClientConfig,
    consumer::{BaseConsumer, CommitMode, Consumer},
    message::Message as KafkaMessage,
};

mod schema {
    pub mod kafka {
        pub mod demo {
            pub mod v1 {
                include!(concat!(env!("OUT_DIR"), "/kafka.demo.v1.rs"));
            }
        }
    }
}

fn env_or(name: &str, fallback: &str) -> String {
    env::var(name).unwrap_or_else(|_| fallback.to_owned())
}

fn main() -> Result<(), Box<dyn Error>> {
    let bootstrap = env_or("BOOTSTRAP_SERVERS", "kafka-kafka-bootstrap.kafka.svc:9092");
    let topic = env_or("TOPIC", "orders");
    let group_id = env_or("GROUP_ID", "kafka-jbod-demo");
    let run_prefix = format!("{}-order-", env_or("RUN_ID", "strimzi-demo"));
    let expected: usize = env_or("EXPECTED_COUNT", "1000000").parse()?;
    let timeout = Duration::from_secs(env_or("TIMEOUT_SECONDS", "1200").parse()?);

    let consumer: BaseConsumer = ClientConfig::new()
        .set("bootstrap.servers", bootstrap)
        .set("group.id", group_id)
        .set("enable.auto.commit", "false")
        .set("enable.partition.eof", "false")
        .set("isolation.level", "read_committed")
        .set("auto.offset.reset", "earliest")
        .create()?;
    consumer.subscribe(&[&topic])?;

    let deadline = Instant::now() + timeout;
    let mut seen = vec![false; expected];
    let mut received = 0;
    let mut total_cents = 0_i64;

    while received < expected && Instant::now() < deadline {
        match consumer.poll(Duration::from_millis(500)) {
            Some(Ok(message)) => {
                let payload = message.payload().ok_or("record has no value")?;
                let order = schema::kafka::demo::v1::Order::decode(payload)?;
                if order.order_id.is_empty() || order.customer_id.is_empty() {
                    return Err("order is missing an id or customer id".into());
                }
                let index: usize = order.order_id.strip_prefix(&run_prefix)
                    .ok_or("order ID does not match this run")?.parse()?;
                if index >= expected || seen[index] {
                    return Err(format!("duplicate or out-of-range order ID: {}", order.order_id).into());
                }
                seen[index] = true;
                received += 1;
                total_cents += order.amount_cents;
                if received % 1000 == 0 {
                    consumer.commit_message(&message, CommitMode::Async)?;
                }
            }
            Some(Err(error)) => return Err(error.into()),
            None => thread::sleep(Duration::from_millis(50)),
        }
    }

    if received != expected {
        return Err(format!(
            "timed out after receiving {} of {expected} orders",
            received
        )
        .into());
    }
    consumer.commit_consumer_state(CommitMode::Sync)?;
    println!(
        "consumed {} unique protobuf orders; amount sum={} cents",
        received,
        total_cents
    );
    Ok(())
}
