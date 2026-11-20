use std::{
    collections::BTreeMap,
    env,
    error::Error,
    thread,
    time::{Duration, Instant},
};

use prost::Message;
use rdkafka::{
    ClientConfig, Offset, TopicPartitionList,
    consumer::{BaseConsumer, CommitMode, Consumer},
    message::Message as KafkaMessage,
};

mod checkpoint;

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

fn validate_order(
    order: &schema::kafka::demo::v1::Order,
    key: Option<&[u8]>,
    prefix: &str,
    expected: usize,
) -> Result<(usize, i64), Box<dyn Error>> {
    let index: usize = order
        .order_id
        .strip_prefix(prefix)
        .ok_or("order ID does not match this run")?
        .parse()?;
    if index >= expected
        || order.order_id != format!("{prefix}{index}")
        || key != Some(order.order_id.as_bytes())
        || order.customer_id != format!("customer-{}", index % 10)
        || order.amount_cents != 1000 + (index % 5000) as i64
        || order.created_at_unix_ms <= index as i64
    {
        return Err(format!("incorrect order body or key: {}", order.order_id).into());
    }
    Ok((index, order.created_at_unix_ms - index as i64))
}

fn validate_runs(
    order: &schema::kafka::demo::v1::Order,
    key: Option<&[u8]>,
    prefixes: &[String],
    expected: usize,
) -> Result<(usize, usize, i64), Box<dyn Error>> {
    prefixes
        .iter()
        .enumerate()
        .find_map(|(run, prefix)| {
            validate_order(order, key, prefix, expected)
                .ok()
                .map(|(index, base)| (run, index, base))
        })
        .ok_or_else(|| format!("incorrect body/key or unknown run: {}", order.order_id).into())
}

fn commit_offsets(
    consumer: &BaseConsumer,
    topic: &str,
    offsets: &BTreeMap<i32, i64>,
) -> Result<(), Box<dyn Error>> {
    let mut positions = TopicPartitionList::new();
    for (&partition, &offset) in offsets {
        positions.add_partition_offset(topic, partition, Offset::Offset(offset))?;
    }
    consumer.commit(&positions, CommitMode::Sync)?;
    Ok(())
}

fn main() -> Result<(), Box<dyn Error>> {
    if env_or("MODE", "validate") == "checkpoint-proof" {
        return checkpoint::run();
    }
    let bootstrap = env_or("BOOTSTRAP_SERVERS", "kafka-kafka-bootstrap.kafka.svc:9092");
    let topic = env_or("TOPIC", "orders");
    let group_id = env_or("GROUP_ID", "kafka-jbod-demo");
    let mut prefixes = vec![format!("{}-order-", env_or("RUN_ID", "strimzi-demo"))];
    if let Ok(second) = env::var("SECOND_RUN_ID") {
        let prefix = format!("{second}-order-");
        if second.is_empty() || prefix == prefixes[0] {
            return Err("SECOND_RUN_ID must be nonempty and distinct".into());
        }
        prefixes.push(prefix);
    }
    let expected: usize = env_or("EXPECTED_COUNT", "1000000").parse()?;
    if !(1..=2_000_000).contains(&expected) {
        return Err("EXPECTED_COUNT must be between 1 and 2000000 per run".into());
    }
    let total_expected = expected * prefixes.len();
    let timeout = Duration::from_secs(env_or("TIMEOUT_SECONDS", "1200").parse()?);

    let consumer: BaseConsumer = ClientConfig::new()
        .set("bootstrap.servers", bootstrap)
        .set("group.id", group_id)
        .set("enable.auto.commit", "false")
        .set("enable.auto.offset.store", "false")
        .set("enable.partition.eof", "false")
        .set("isolation.level", "read_committed")
        .set("auto.offset.reset", "earliest")
        .create()?;
    consumer.subscribe(&[&topic])?;

    let deadline = Instant::now() + timeout;
    let mut seen = vec![vec![false; expected]; prefixes.len()];
    let mut received = 0;
    let mut total_cents = 0_i64;
    let mut created_bases = vec![None; prefixes.len()];
    let mut offsets = BTreeMap::new();

    while received < total_expected && Instant::now() < deadline {
        match consumer.poll(Duration::from_millis(500)) {
            Some(Ok(message)) => {
                let payload = message.payload().ok_or("record has no value")?;
                let order = schema::kafka::demo::v1::Order::decode(payload)?;
                let (run, index, base) = validate_runs(&order, message.key(), &prefixes, expected)?;
                if seen[run][index] || created_bases[run].is_some_and(|known| known != base) {
                    return Err(format!(
                        "duplicate order or inconsistent creation timestamp: {}",
                        order.order_id
                    )
                    .into());
                }
                created_bases[run] = Some(base);
                if offsets
                    .get(&message.partition())
                    .is_some_and(|&previous| message.offset() < previous)
                {
                    return Err("partition offsets moved backwards".into());
                }
                offsets.insert(message.partition(), message.offset() + 1);
                seen[run][index] = true;
                received += 1;
                total_cents += order.amount_cents;
                if received % 1000 == 0 {
                    commit_offsets(&consumer, &topic, &offsets)?;
                }
            }
            Some(Err(error)) => return Err(error.into()),
            None => thread::sleep(Duration::from_millis(50)),
        }
    }

    if received != total_expected {
        return Err(format!(
            "timed out after receiving {} of {total_expected} orders",
            received
        )
        .into());
    }
    commit_offsets(&consumer, &topic, &offsets)?;
    println!(
        "consumed {} unique protobuf orders; amount sum={} cents",
        received, total_cents
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn valid() -> schema::kafka::demo::v1::Order {
        schema::kafka::demo::v1::Order {
            order_id: "trial-order-5001".to_owned(),
            customer_id: "customer-1".to_owned(),
            amount_cents: 1001,
            created_at_unix_ms: 1_800_000_005_001,
        }
    }

    #[test]
    fn validates_complete_body_and_key() {
        let order = valid();
        assert_eq!(
            validate_order(
                &order,
                Some(order.order_id.as_bytes()),
                "trial-order-",
                10000
            )
            .unwrap(),
            (5001, 1_800_000_000_000)
        );
        assert!(validate_order(&order, None, "trial-order-", 10000).is_err());
        assert!(validate_order(&order, Some(b"wrong-key"), "trial-order-", 10000).is_err());
        assert!(
            validate_order(
                &order,
                Some(order.order_id.as_bytes()),
                "trial-order-",
                5001
            )
            .is_err()
        );
    }

    #[test]
    fn two_run_validation_accepts_only_declared_bodies() {
        let prefixes = vec!["trial-order-".to_owned(), "trial-west-order-".to_owned()];
        let mut order = valid();
        assert_eq!(
            validate_runs(&order, Some(order.order_id.as_bytes()), &prefixes, 10000)
                .unwrap()
                .0,
            0
        );
        order.order_id = "trial-west-order-5001".to_owned();
        assert_eq!(
            validate_runs(&order, Some(order.order_id.as_bytes()), &prefixes, 10000)
                .unwrap()
                .0,
            1
        );
        order.amount_cents = 1;
        assert!(validate_runs(&order, Some(order.order_id.as_bytes()), &prefixes, 10000).is_err());
        order = valid();
        order.order_id = "unknown-order-5001".to_owned();
        assert!(validate_runs(&order, Some(order.order_id.as_bytes()), &prefixes, 10000).is_err());
    }

    #[test]
    fn rejects_noncanonical_order_ids() {
        let mut order = valid();
        order.order_id = "trial-order-05001".to_owned();
        assert!(
            validate_order(
                &order,
                Some(order.order_id.as_bytes()),
                "trial-order-",
                10000
            )
            .is_err()
        );
    }

    #[test]
    fn rejects_correct_id_with_wrong_values() {
        for field in 0..3 {
            let mut order = valid();
            match field {
                0 => order.customer_id = "customer-2".to_owned(),
                1 => order.amount_cents = 1002,
                _ => order.created_at_unix_ms = 0,
            }
            assert!(
                validate_order(
                    &order,
                    Some(order.order_id.as_bytes()),
                    "trial-order-",
                    10000
                )
                .is_err()
            );
        }
    }
}
