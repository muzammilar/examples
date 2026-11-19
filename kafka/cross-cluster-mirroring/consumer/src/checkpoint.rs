use std::{
    collections::BTreeMap,
    env,
    error::Error,
    time::{Duration, Instant},
};

use rdkafka::{
    ClientConfig, Offset, TopicPartitionList,
    consumer::{BaseConsumer, Consumer},
    error::KafkaError,
};

fn consumer(bootstrap: &str, group: &str) -> Result<BaseConsumer, Box<dyn Error>> {
    Ok(ClientConfig::new()
        .set("bootstrap.servers", bootstrap)
        .set("group.id", group)
        .set("enable.auto.commit", "false")
        .set("enable.auto.offset.store", "false")
        .set("enable.partition.eof", "true")
        .set("isolation.level", "read_committed")
        .set("auto.offset.reset", "error")
        .create()?)
}

fn offsets(raw: &str) -> Result<BTreeMap<i32, (i64, i64)>, Box<dyn Error>> {
    let mut result = BTreeMap::new();
    for row in raw.split(',') {
        let fields: Vec<_> = row.split(':').collect();
        if fields.len() != 3 {
            return Err("checkpoint must be partition:upstream:downstream".into());
        }
        let partition: i32 = fields[0].parse()?;
        let upstream: i64 = fields[1].parse()?;
        let downstream: i64 = fields[2].parse()?;
        if !(0..6).contains(&partition)
            || upstream < 0
            || downstream < 0
            || result.insert(partition, (upstream, downstream)).is_some()
        {
            return Err("invalid or repeated checkpoint partition/offset".into());
        }
    }
    if result.len() != 6 {
        return Err("expected checkpoints for all six partitions".into());
    }
    Ok(result)
}

fn inspect(
    consumer: &BaseConsumer,
    topic: &str,
    group: &str,
    expected: &BTreeMap<i32, i64>,
) -> Result<(), Box<dyn Error>> {
    let timeout = Duration::from_secs(10);
    let metadata = consumer.fetch_metadata(Some(topic), timeout)?;
    let native = metadata
        .topics()
        .iter()
        .find(|entry| entry.name() == topic)
        .ok_or("topic missing")?;
    if native.error().is_some() || native.partitions().len() != 6 {
        return Err("invalid topic metadata".into());
    }
    for partition in native.partitions() {
        let mut replicas = partition.replicas().to_vec();
        let mut isr = partition.isr().to_vec();
        replicas.sort_unstable();
        isr.sort_unstable();
        if partition.error().is_some()
            || replicas.len() != 3
            || replicas != isr
            || !isr.contains(&partition.leader())
        {
            return Err(format!("partition {} has not restored RF=3/ISR=3", partition.id()).into());
        }
    }
    let groups = consumer.fetch_group_list(Some(group), timeout)?;
    let native_group = groups
        .groups()
        .iter()
        .find(|entry| entry.name() == group)
        .ok_or("group missing")?;
    if native_group.state() != "Empty" || !native_group.members().is_empty() {
        return Err("consumer group is not idle and healthy".into());
    }
    let mut assignment = TopicPartitionList::new();
    for &partition in expected.keys() {
        assignment.add_partition(topic, partition);
    }
    let committed = consumer.committed_offsets(assignment, timeout)?;
    for (&partition, &offset) in expected {
        if committed
            .find_partition(topic, partition)
            .ok_or("missing committed partition")?
            .offset()
            != Offset::Offset(offset)
        {
            return Err(format!(
                "committed offset differs from checkpoint for {topic}/{partition}"
            )
            .into());
        }
    }
    Ok(())
}

pub fn run() -> Result<(), Box<dyn Error>> {
    let source = env::var("SOURCE_BOOTSTRAP")?;
    let target = env::var("BOOTSTRAP_SERVERS")?;
    let source_topic = env::var("SOURCE_TOPIC")?;
    let target_topic = env::var("TOPIC")?;
    let group = env::var("GROUP_ID")?;
    let checkpoints = offsets(&env::var("CHECKPOINT_OFFSETS")?)?;
    let upstream: BTreeMap<_, _> = checkpoints
        .iter()
        .map(|(&p, &(offset, _))| (p, offset))
        .collect();
    let downstream: BTreeMap<_, _> = checkpoints
        .iter()
        .map(|(&p, &(_, offset))| (p, offset))
        .collect();
    let source_consumer = consumer(&source, &group)?;
    let target_consumer = consumer(&target, &group)?;
    inspect(&source_consumer, &source_topic, &group, &upstream)?;
    inspect(&target_consumer, &target_topic, &group, &downstream)?;
    let mut assignment = TopicPartitionList::new();
    for (&partition, &offset) in &downstream {
        assignment.add_partition_offset(&target_topic, partition, Offset::Offset(offset))?;
    }
    target_consumer.assign(&assignment)?;
    let mut ends = BTreeMap::new();
    let deadline = Instant::now() + Duration::from_secs(30);
    while ends.len() < 6 && Instant::now() < deadline {
        match target_consumer.poll(Duration::from_millis(500)) {
            Some(Ok(_)) => {
                return Err("translated checkpoint would replay an application record".into());
            }
            Some(Err(KafkaError::PartitionEOF(partition))) => {
                let positions = target_consumer.position()?;
                let position = positions
                    .find_partition(&target_topic, partition)
                    .ok_or("EOF partition missing")?
                    .offset();
                let Offset::Offset(end) = position else {
                    return Err("invalid EOF position".into());
                };
                if end < downstream[&partition] {
                    return Err("EOF precedes committed checkpoint".into());
                }
                ends.insert(partition, end);
            }
            Some(Err(error)) => return Err(error.into()),
            None => {}
        }
    }
    if ends.len() != 6 {
        return Err("did not observe read-committed EOF on all six partitions".into());
    }
    inspect(&source_consumer, &source_topic, &group, &upstream)?;
    inspect(&target_consumer, &target_topic, &group, &downstream)?;
    println!("partition\tupstream_committed\ttranslated_committed\tobserved_read_committed_end");
    for (&partition, &(upstream, downstream)) in &checkpoints {
        println!(
            "{partition}\t{upstream}\t{downstream}\t{}",
            ends[&partition]
        );
    }
    println!("CHECKPOINT_PROOF partitions=6 replay_records=0 native_decoder=KafkaCheckpoint");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_incomplete_duplicate_and_negative_checkpoints() {
        assert!(offsets("0:1:2").is_err());
        assert!(offsets("0:1:2,1:1:2,2:1:2,3:1:2,4:1:2,4:1:2").is_err());
        assert!(offsets("0:-1:2,1:1:2,2:1:2,3:1:2,4:1:2,5:1:2").is_err());
        assert_eq!(
            offsets("0:1:2,1:1:2,2:1:2,3:1:2,4:1:2,5:1:2")
                .unwrap()
                .len(),
            6
        );
    }
}
