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
    let consumer: BaseConsumer = ClientConfig::new()
        .set(
            "bootstrap.servers",
            env_or("BOOTSTRAP_SERVERS", "kafka-broker-1:19092"),
        )
        .set(
            "group.id",
            env_or("GROUP_ID", "kafka-connect-demo-consumer"),
        )
        .set("enable.auto.commit", "false")
        .set("auto.offset.reset", "earliest")
        .create()?;
    let topic = env_or("TOPIC", "orders.enriched");
    let run_id = env_or("RUN_ID", "connect-demo");
    let run_prefix = format!("{run_id}-order-");
    let expected: usize = env_or("EXPECTED_COUNT", "2000000").parse()?;
    let timeout = Duration::from_secs(env_or("TIMEOUT_SECONDS", "1800").parse()?);
    consumer.subscribe(&[&topic])?;

    let deadline = Instant::now() + timeout;
    let mut seen = vec![false; expected];
    let mut received = 0;
    while received < expected && Instant::now() < deadline {
        match consumer.poll(Duration::from_millis(500)) {
            Some(Ok(message)) => {
                let payload = message.payload().ok_or("record has no value")?;
                let enriched = schema::kafka::demo::v1::EnrichedOrder::decode(payload)?;
                let order = enriched.order.ok_or("enriched record has no order")?;
                let customer = enriched.customer.ok_or("enriched record has no customer")?;
                if !order.order_id.starts_with(&run_prefix) {
                    consumer.commit_message(&message, CommitMode::Sync)?;
                    continue;
                }
                if order.order_id.is_empty() || customer.customer_id != order.customer_id
                    || customer.display_name.is_empty() || customer.segment.is_empty() {
                    return Err("order/customer IDs do not match".into());
                }
                let index: usize = order.order_id.strip_prefix(&run_prefix)
                    .ok_or("order ID does not match this run")?.parse()?;
                if index >= expected || seen[index] {
                    return Err(format!("duplicate or out-of-range order ID: {}", order.order_id).into());
                }
                seen[index] = true;
                received += 1;
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
            "timed out after receiving {} of {expected} enriched orders",
            received
        )
        .into());
    }
    consumer.commit_consumer_state(CommitMode::Sync)?;
    println!("consumed {received} unique enriched protobuf orders");
    Ok(())
}
