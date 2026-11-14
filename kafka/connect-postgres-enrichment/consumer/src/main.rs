use std::{
    collections::HashSet,
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
    let expected: usize = env_or("EXPECTED_COUNT", "100").parse()?;
    let timeout = Duration::from_secs(env_or("TIMEOUT_SECONDS", "180").parse()?);
    consumer.subscribe(&[&topic])?;

    let deadline = Instant::now() + timeout;
    let mut seen = HashSet::with_capacity(expected);
    while seen.len() < expected && Instant::now() < deadline {
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
                if order.order_id.is_empty() || customer.customer_id != order.customer_id {
                    return Err("order/customer IDs do not match".into());
                }
                if !seen.insert(order.order_id.clone()) {
                    return Err(format!("duplicate order id: {}", order.order_id).into());
                }
                println!(
                    "{} -> {} ({})",
                    order.order_id, customer.display_name, customer.segment
                );
                consumer.commit_message(&message, CommitMode::Sync)?;
            }
            Some(Err(error)) => return Err(error.into()),
            None => thread::sleep(Duration::from_millis(50)),
        }
    }
    if seen.len() != expected {
        return Err(format!(
            "timed out after receiving {} of {expected} enriched orders",
            seen.len()
        )
        .into());
    }
    println!("consumed {} unique enriched protobuf orders", seen.len());
    Ok(())
}
