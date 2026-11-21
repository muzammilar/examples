use crate::{
    schema,
    witness::{Result, now},
};
use flate2::read::GzDecoder;
use prost::Message;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs::File,
    io::{BufRead, BufReader},
    path::Path,
};

#[derive(Deserialize, Clone, PartialEq)]
pub struct Customer {
    pub customer_id: String,
    pub display_name: String,
    pub segment: String,
}
fn default_customers() -> usize {
    10
}

#[derive(Deserialize)]
pub struct Plan {
    pub format_version: u32,
    pub run_id: String,
    pub topic: String,
    pub bootstrap_servers: String,
    pub minimum_messages: usize,
    pub warmup_messages: usize,
    pub live_rate: usize,
    pub burst_rate: usize,
    pub duration_seconds: u64,
    pub partitions: i32,
    pub group_id: String,
    #[serde(default)]
    pub mirrored: bool,
    #[serde(default = "default_customers")]
    pub customer_count: usize,
    #[serde(default)]
    pub enriched: bool,
    #[serde(skip)]
    pub customers: BTreeMap<String, Customer>,
}
impl Plan {
    pub fn load(root: &Path) -> Result<Self> {
        let mut plan: Self = serde_json::from_reader(File::open(root.join("case.json"))?)?;
        if plan.format_version != 1
            || plan.run_id.is_empty()
            || plan.topic.is_empty()
            || plan.bootstrap_servers.is_empty()
            || plan.group_id.is_empty()
            || !(1..=2_000_000).contains(&plan.minimum_messages)
            || !(1..=plan.minimum_messages).contains(&plan.warmup_messages)
            || !(1..=100_000).contains(&plan.live_rate)
            || plan.burst_rate > 1_000_000
            || !(60..=7200).contains(&plan.duration_seconds)
            || !(1..=64).contains(&plan.partitions)
            || !(1..=1_000_000).contains(&plan.customer_count)
        {
            return Err("invalid case plan".into());
        }
        if plan.enriched {
            plan.customers = serde_json::from_reader(File::open(root.join("customers.json"))?)?;
            for n in 0..plan.customer_count {
                let id = format!("customer-{n}");
                let customer = plan
                    .customers
                    .get(&id)
                    .ok_or("customer missing from independent SQL oracle")?;
                if customer.customer_id != id {
                    return Err("customer oracle key/body mismatch".into());
                }
            }
        }
        Ok(plan)
    }
}

#[derive(Serialize)]
pub struct Observed {
    pub stage: &'static str,
    pub time: f64,
    pub seconds: f64,
    pub seq: usize,
    pub id: String,
    pub sha256: String,
    pub created_ms: i64,
    pub bytes: usize,
    pub wire_bytes: usize,
    pub partition: i32,
    pub offset: i64,
    pub duplicate: bool,
}

pub fn decode(
    plan: &Plan,
    payload: &[u8],
    key: Option<&[u8]>,
    partition: i32,
    offset: i64,
    seconds: f64,
) -> Result<Observed> {
    let (order, order_bytes) = if plan.enriched {
        let enriched = schema::kafka::demo::v1::EnrichedOrder::decode(payload)?;
        if enriched.encode_to_vec() != payload {
            return Err("noncanonical or unknown enriched fields".into());
        }
        let order = enriched.order.ok_or("missing enriched order")?;
        let customer = enriched.customer.ok_or("missing enriched customer")?;
        let expected = plan
            .customers
            .get(&order.customer_id)
            .ok_or("unrecognized customer")?;
        if customer.customer_id != order.customer_id
            || customer.display_name != expected.display_name
            || customer.segment != expected.segment
        {
            return Err(format!(
                "stale/wrong customer {}: name={:?}, expected={:?}",
                order.customer_id, customer.display_name, expected.display_name
            )
            .into());
        }
        let bytes = order.encode_to_vec();
        (order, bytes)
    } else {
        (
            schema::kafka::demo::v1::Order::decode(payload)?,
            payload.to_vec(),
        )
    };
    let prefix = format!("{}-order-", plan.run_id);
    let suffix = order.order_id.strip_prefix(&prefix).ok_or("wrong run ID")?;
    let seq: usize = suffix.parse()?;
    if suffix != seq.to_string()
        || seq >= plan.minimum_messages * 4
        || partition < 0
        || partition >= plan.partitions
        || offset < 0
        || key != Some(order.order_id.as_bytes())
        || order.customer_id != format!("customer-{}", seq % plan.customer_count)
        || order.amount_cents != 1000 + (seq % 5000) as i64
        || order.created_at_unix_ms <= 0
    {
        return Err("wrong key, ID, partition, offset or complete order body".into());
    }
    Ok(Observed {
        stage: "message",
        time: now(),
        seconds,
        seq,
        id: order.order_id,
        sha256: format!("{:x}", Sha256::digest(&order_bytes)),
        created_ms: order.created_at_unix_ms,
        bytes: order_bytes.len(),
        wire_bytes: payload.len(),
        partition,
        offset,
        duplicate: false,
    })
}

#[derive(Deserialize)]
struct ProducerEvent {
    stage: String,
    seq: usize,
    id: String,
    sha256: String,
    created_ms: i64,
    bytes: usize,
    partition: Option<i32>,
    offset: Option<i64>,
    offset_known: Option<bool>,
    persistence_status: Option<i32>,
}

pub struct Expected {
    digest: String,
    created_ms: i64,
    bytes: usize,
    outcome: Option<String>,
    delivery: Option<(i32, Option<i64>)>,
    seen: Option<(i32, i64)>,
    ack_seen: bool,
}

pub struct Oracle {
    mirrored: bool,
    rows: Vec<Option<Expected>>,
    pub acknowledged: usize,
    pub ambiguous: usize,
    pub observed_acknowledged: usize,
    pub observed_nonack: usize,
    pub duplicate_deliveries: usize,
    pub unique_messages: usize,
    pub duplicate_logical_records: usize,
    pub duplicate_acknowledged_records: usize,
}
impl Oracle {
    pub fn load(root: &Path, plan: &Plan) -> Result<Self> {
        let mut result = Self {
            mirrored: plan.mirrored,
            rows: Vec::new(),
            acknowledged: 0,
            ambiguous: 0,
            observed_acknowledged: 0,
            observed_nonack: 0,
            duplicate_deliveries: 0,
            unique_messages: 0,
            duplicate_logical_records: 0,
            duplicate_acknowledged_records: 0,
        };
        let reader = BufReader::new(GzDecoder::new(File::open(root.join("producer.jsonl.gz"))?));
        for line in reader.lines() {
            let event: ProducerEvent = serde_json::from_str(&line?)?;
            if event.seq >= plan.minimum_messages * 4
                || event.id != format!("{}-order-{}", plan.run_id, event.seq)
                || event.sha256.len() != 64
                || !event
                    .sha256
                    .bytes()
                    .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
                || event.created_ms <= 0
                || event.bytes == 0
            {
                return Err("invalid durable producer record".into());
            }
            if result.rows.len() <= event.seq {
                result.rows.resize_with(event.seq + 1, || None);
            }
            if event.stage == "intent" {
                if result.rows[event.seq].is_some() {
                    return Err("reused intended sequence".into());
                }
                result.rows[event.seq] = Some(Expected {
                    digest: event.sha256,
                    created_ms: event.created_ms,
                    bytes: event.bytes,
                    outcome: None,
                    delivery: None,
                    seen: None,
                    ack_seen: false,
                });
            } else {
                let expected = result.rows[event.seq]
                    .as_mut()
                    .ok_or("outcome without intent")?;
                if expected.outcome.is_some()
                    || expected.digest != event.sha256
                    || expected.created_ms != event.created_ms
                    || expected.bytes != event.bytes
                {
                    return Err("duplicate or changed delivery outcome".into());
                }
                match event.stage.as_str() {
                    "ack" => {
                        let partition = event.partition.ok_or("ACK has no partition")?;
                        let offset = event.offset;
                        if partition < 0
                            || partition >= plan.partitions
                            || offset.is_some_and(|o| o < 0)
                            || (offset.is_none()
                                && (event.offset_known != Some(false)
                                    || event.persistence_status != Some(2)))
                        {
                            return Err("invalid ACK position".into());
                        }
                        expected.delivery = Some((partition, offset));
                        result.acknowledged += 1;
                    }
                    "ambiguous" => {
                        result.ambiguous += 1;
                    }
                    "rejected" => {}
                    _ => return Err("unknown producer stage".into()),
                }
                expected.outcome = Some(event.stage);
            }
        }
        if result.rows.iter().any(Option::is_none) {
            return Err("gap in intended IDs".into());
        }
        result.ambiguous += result
            .rows
            .iter()
            .filter(|r| r.as_ref().is_some_and(|e| e.outcome.is_none()))
            .count();
        Ok(result)
    }
    /// Read-only diagnosis never establishes a passing strict verification.
    pub fn diagnose(&mut self, row: &Observed) -> Result<()> {
        let expected = self
            .rows
            .get_mut(row.seq)
            .and_then(Option::as_mut)
            .ok_or("unexpected diagnostic record")?;
        if row.sha256 != expected.digest
            || row.created_ms != expected.created_ms
            || row.bytes != expected.bytes
            || expected.outcome.as_deref() == Some("rejected")
        {
            return Err("diagnosis found corrupt or unexpected data".into());
        }
        let position = (row.partition, row.offset);
        match expected.seen {
            None => {
                self.unique_messages += 1;
                expected.seen = Some(position);
                if expected.delivery.is_none() {
                    self.observed_nonack += 1;
                }
            }
            Some(old) if old == position => {
                self.duplicate_deliveries += 1;
            }
            Some(_) => {
                self.duplicate_logical_records += 1;
                if expected.delivery.is_some() {
                    self.duplicate_acknowledged_records += 1;
                }
            }
        }
        if let Some((partition, offset)) = expected.delivery
            && partition == row.partition
            && (self.mirrored || offset.is_none_or(|o| o == row.offset))
            && !expected.ack_seen
        {
            expected.ack_seen = true;
            self.observed_acknowledged += 1;
        }
        Ok(())
    }
    pub fn observe(&mut self, row: &Observed) -> Result<()> {
        let expected = self
            .rows
            .get_mut(row.seq)
            .and_then(Option::as_mut)
            .ok_or("unexpected Kafka record")?;
        if row.sha256 != expected.digest
            || row.created_ms != expected.created_ms
            || row.bytes != expected.bytes
            || expected.outcome.as_deref() == Some("rejected")
        {
            return Err("body differs from durable intent or a rejected write applied".into());
        }
        let position = (row.partition, row.offset);
        if let Some(previous) = expected.seen {
            if previous != position {
                return Err("logical order duplicated at another Kafka offset".into());
            }
            self.duplicate_deliveries += 1;
            return Ok(());
        }
        if let Some(acknowledged) = expected.delivery {
            if acknowledged.0 != position.0
                || (!self.mirrored && acknowledged.1.is_some_and(|offset| offset != position.1))
            {
                return Err("ACK position differs from replayed record".into());
            }
            self.observed_acknowledged += 1;
        } else {
            self.observed_nonack += 1;
        }
        expected.seen = Some(position);
        self.unique_messages += 1;
        Ok(())
    }
    pub fn finish(&self) -> Result<()> {
        if self.observed_acknowledged != self.acknowledged {
            return Err("acknowledged orders missing after recovery".into());
        }
        Ok(())
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::witness::Journal;
    use serde_json::json;
    use std::fs;

    pub fn plan() -> Plan {
        Plan {
            format_version: 1,
            run_id: "test".into(),
            topic: "orders".into(),
            bootstrap_servers: "unused:9092".into(),
            minimum_messages: 100,
            warmup_messages: 50,
            live_rate: 1000,
            burst_rate: 0,
            duration_seconds: 60,
            partitions: 2,
            group_id: "test".into(),
            mirrored: false,
            customer_count: 10,
            enriched: false,
            customers: BTreeMap::new(),
        }
    }
    #[test]
    fn checks_customer_join_and_source_digest() -> Result<()> {
        let mut plan = plan();
        plan.enriched = true;
        plan.customers.insert(
            "customer-0".into(),
            Customer {
                customer_id: "customer-0".into(),
                display_name: "Customer 0 updated".into(),
                segment: "gold".into(),
            },
        );
        let order = schema::kafka::demo::v1::Order {
            order_id: "test-order-0".into(),
            customer_id: "customer-0".into(),
            amount_cents: 1000,
            created_at_unix_ms: 1234,
        };
        let mut enriched = schema::kafka::demo::v1::EnrichedOrder {
            order: Some(order.clone()),
            customer: Some(schema::kafka::demo::v1::Customer {
                customer_id: "customer-0".into(),
                display_name: "Customer 0 updated".into(),
                segment: "gold".into(),
            }),
        };
        let observed = decode(
            &plan,
            &enriched.encode_to_vec(),
            Some(b"test-order-0"),
            0,
            1,
            0.,
        )?;
        assert_eq!(
            observed.sha256,
            format!("{:x}", Sha256::digest(order.encode_to_vec()))
        );
        enriched.customer.as_mut().unwrap().display_name = "Customer 0".into();
        assert!(
            decode(
                &plan,
                &enriched.encode_to_vec(),
                Some(b"test-order-0"),
                0,
                1,
                0.
            )
            .is_err()
        );
        Ok(())
    }

    pub fn row(seq: usize, partition: i32, offset: i64) -> Result<Observed> {
        let plan = plan();
        let order = schema::kafka::demo::v1::Order {
            order_id: format!("test-order-{seq}"),
            customer_id: format!("customer-{}", seq % 10),
            amount_cents: 1000 + (seq % 5000) as i64,
            created_at_unix_ms: 1234,
        };
        decode(
            &plan,
            &order.encode_to_vec(),
            Some(order.order_id.as_bytes()),
            partition,
            offset,
            0.,
        )
    }
    #[test]
    fn rejects_wrong_key_customer_amount_and_missing_time() -> Result<()> {
        let plan = plan();
        let base = schema::kafka::demo::v1::Order {
            order_id: "test-order-0".into(),
            customer_id: "customer-0".into(),
            amount_cents: 1000,
            created_at_unix_ms: 1234,
        };
        assert!(decode(&plan, &base.encode_to_vec(), Some(b"wrong"), 0, 0, 0.).is_err());
        let mut changed = base.clone();
        changed.customer_id = "customer-1".into();
        assert!(
            decode(
                &plan,
                &changed.encode_to_vec(),
                Some(base.order_id.as_bytes()),
                0,
                0,
                0.
            )
            .is_err()
        );
        changed = base.clone();
        changed.amount_cents += 1;
        assert!(
            decode(
                &plan,
                &changed.encode_to_vec(),
                Some(base.order_id.as_bytes()),
                0,
                0,
                0.
            )
            .is_err()
        );
        changed = base.clone();
        changed.created_at_unix_ms = 0;
        assert!(
            decode(
                &plan,
                &changed.encode_to_vec(),
                Some(base.order_id.as_bytes()),
                0,
                0,
                0.
            )
            .is_err()
        );
        assert!(
            decode(
                &plan,
                b"invalid protobuf",
                Some(base.order_id.as_bytes()),
                0,
                0,
                0.
            )
            .is_err()
        );
        Ok(())
    }
    #[test]
    fn replay_matches_full_intent_and_ack_location() -> Result<()> {
        let root = std::env::temp_dir().join(format!("kafka-order-oracle-{}", std::process::id()));
        fs::create_dir(&root)?;
        let observed = row(0, 0, 4)?;
        let mut journal = Journal::new(&root.join("producer.jsonl.gz"))?;
        let intent = json!({"stage":"intent","seq":observed.seq,"id":observed.id,"sha256":observed.sha256,
            "created_ms":observed.created_ms,"bytes":observed.bytes});
        journal.append(&intent)?;
        let mut ack = intent;
        ack["stage"] = json!("ack");
        ack["partition"] = json!(0);
        ack["offset"] = json!(4);
        journal.append(&ack)?;
        journal.finish()?;
        let mut oracle = Oracle::load(&root, &plan())?;
        assert!(oracle.finish().is_err());
        let mut wrong = row(0, 0, 5)?;
        assert!(oracle.observe(&wrong).is_err());
        wrong = row(0, 0, 4)?;
        wrong.sha256 = "0".repeat(64);
        assert!(oracle.observe(&wrong).is_err());
        let mut mirror_plan = plan();
        mirror_plan.mirrored = true;
        let mut mirror = Oracle::load(&root, &mirror_plan)?;
        assert!(mirror.observe(&row(0, 1, 50)?).is_err());
        mirror.observe(&row(0, 0, 50)?)?;
        assert!(mirror.observe(&row(0, 0, 51)?).is_err());
        mirror.finish()?;
        oracle.observe(&observed)?;
        oracle.observe(&observed)?;
        oracle.finish()?;
        assert_eq!(oracle.observed_acknowledged, 1);
        assert_eq!(oracle.duplicate_deliveries, 1);
        fs::remove_dir_all(root)?;
        Ok(())
    }
    #[test]
    fn persisted_ack_without_offset_still_requires_full_unique_body() -> Result<()> {
        let root =
            std::env::temp_dir().join(format!("kafka-order-unknown-offset-{}", std::process::id()));
        fs::create_dir(&root)?;
        let observed = row(0, 0, 4)?;
        let path = root.join("producer.jsonl.gz");
        for persisted in [1, 2] {
            let mut journal = Journal::new(&path)?;
            let intent = json!({"stage":"intent","seq":0,"id":observed.id,"sha256":observed.sha256,
                "created_ms":observed.created_ms,"bytes":observed.bytes});
            journal.append(&intent)?;
            let mut ack = intent;
            ack["stage"] = json!("ack");
            ack["partition"] = json!(0);
            ack["offset"] = json!(null);
            ack["offset_known"] = json!(false);
            ack["persistence_status"] = json!(persisted);
            journal.append(&ack)?;
            journal.finish()?;
            if persisted == 1 {
                assert!(Oracle::load(&root, &plan()).is_err());
            } else {
                let mut oracle = Oracle::load(&root, &plan())?;
                assert!(oracle.finish().is_err());
                assert!(oracle.observe(&row(0, 1, 4)?).is_err());
                oracle.observe(&observed)?;
                assert!(oracle.observe(&row(0, 0, 5)?).is_err());
                oracle.finish()?;
                assert_eq!(oracle.observed_acknowledged, 1);
            }
            fs::remove_file(&path)?;
        }
        fs::remove_dir(root)?;
        Ok(())
    }
    #[test]
    fn diagnosis_counts_duplicates_without_claiming_a_strict_pass() -> Result<()> {
        let root =
            std::env::temp_dir().join(format!("kafka-order-diagnosis-{}", std::process::id()));
        fs::create_dir(&root)?;
        let observed = row(0, 0, 4)?;
        let path = root.join("producer.jsonl.gz");
        for stage in ["ack", "ambiguous"] {
            let mut journal = Journal::new(&path)?;
            let intent = json!({"stage":"intent","seq":0,"id":observed.id,"sha256":observed.sha256,"created_ms":observed.created_ms,"bytes":observed.bytes});
            journal.append(&intent)?;
            let mut outcome = intent;
            outcome["stage"] = json!(stage);
            outcome["partition"] = json!(0);
            outcome["offset"] = json!(4);
            journal.append(&outcome)?;
            journal.finish()?;
            let mut strict = Oracle::load(&root, &plan())?;
            strict.observe(&observed)?;
            assert!(strict.observe(&row(0, 0, 5)?).is_err());
            let mut diagnostic = Oracle::load(&root, &plan())?;
            diagnostic.diagnose(&observed)?;
            diagnostic.diagnose(&row(0, 0, 5)?)?;
            assert_eq!(diagnostic.duplicate_logical_records, 1);
            assert_eq!(
                diagnostic.duplicate_acknowledged_records,
                usize::from(stage == "ack")
            );
            assert_eq!(
                diagnostic.observed_acknowledged,
                usize::from(stage == "ack")
            );
            let mut corrupt = row(0, 0, 6)?;
            corrupt.sha256 = "0".repeat(64);
            assert!(diagnostic.diagnose(&corrupt).is_err());
            fs::remove_file(&path)?;
        }
        fs::remove_dir(root)?;
        Ok(())
    }
    #[test]
    fn incomplete_gzip_or_outcome_without_intent_fails() -> Result<()> {
        let root = std::env::temp_dir().join(format!("kafka-order-corrupt-{}", std::process::id()));
        fs::create_dir(&root)?;
        let path = root.join("producer.jsonl.gz");
        let observed = row(0, 0, 4)?;
        let mut journal = Journal::new(&path)?;
        journal.append(
            &json!({"stage":"ack","seq":0,"id":observed.id,"sha256":observed.sha256,
            "created_ms":observed.created_ms,"bytes":observed.bytes,"partition":0,"offset":4}),
        )?;
        journal.finish()?;
        assert!(Oracle::load(&root, &plan()).is_err());
        fs::remove_file(&path)?;
        let mut journal = Journal::new(&path)?;
        let intent = json!({"stage":"intent","seq":0,"id":observed.id,"sha256":observed.sha256,
            "created_ms":observed.created_ms,"bytes":observed.bytes});
        journal.append(&intent)?;
        let mut ack = intent;
        ack["stage"] = json!("ack");
        ack["partition"] = json!(0);
        ack["offset"] = json!(4);
        journal.append(&ack)?;
        journal.finish()?;
        assert!(Oracle::load(&root, &plan()).is_ok());
        let mut bytes = fs::read(&path)?;
        bytes.truncate(bytes.len() - 8);
        fs::write(&path, bytes)?;
        assert!(Oracle::load(&root, &plan()).is_err());
        fs::remove_dir_all(root)?;
        Ok(())
    }
}
