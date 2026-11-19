mod orders;
mod witness;

use orders::{Observed, Oracle, Plan};
use rdkafka::{
    ClientConfig, ClientContext,
    consumer::{BaseConsumer, CommitMode, Consumer, ConsumerContext, Rebalance},
    error::{KafkaError, KafkaResult},
    message::Message as _,
    topic_partition_list::{Offset, TopicPartitionList},
};
use serde_json::{Value, json};
use sha2::Digest;
use std::{
    collections::BTreeMap,
    env,
    path::{Path, PathBuf},
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicI64, AtomicU64, Ordering},
    },
    time::{Duration, Instant},
};
use witness::{Journal, Result, atomic_json, now};

mod schema {
    pub mod kafka {
        pub mod demo {
            pub mod v1 {
                include!(concat!(env!("OUT_DIR"), "/kafka.demo.v1.rs"));
            }
        }
    }
}

struct NativeState {
    journal: Mutex<Journal>,
    failed: AtomicBool,
    rebalance: AtomicU64,
    last_statistics: AtomicI64,
}
impl NativeState {
    fn record(&self, event: Value) {
        let result = (|| -> Result<()> {
            let mut journal = self
                .journal
                .lock()
                .map_err(|_| "native witness lock poisoned")?;
            journal.append(&event)?;
            journal.sync()?;
            Ok(())
        })();
        if let Err(error) = result {
            self.failed.store(true, Ordering::Relaxed);
            eprintln!("native witness: {error}");
        }
    }
    fn check(&self) -> Result<()> {
        if self.failed.load(Ordering::Relaxed) {
            return Err("native observer failed".into());
        }
        Ok(())
    }
    fn finish(&self) -> Result<()> {
        self.journal
            .lock()
            .map_err(|_| "native witness lock poisoned")?
            .finish()
    }
}
#[derive(Clone)]
struct LabContext(Arc<NativeState>);
impl ClientContext for LabContext {
    fn stats_raw(&self, bytes: &[u8]) {
        match serde_json::from_slice::<Value>(bytes) {
            Ok(data) => {
                if let Some(generated) = data["time"].as_i64() {
                    self.0
                        .record(json!({"time":now(),"kind":"statistics","data":data}));
                    self.0.last_statistics.store(generated, Ordering::Relaxed);
                } else {
                    self.0.failed.store(true, Ordering::Relaxed);
                    eprintln!("native statistics lack generation time");
                }
            }
            Err(error) => {
                self.0.failed.store(true, Ordering::Relaxed);
                eprintln!("native statistics JSON: {error}");
            }
        }
    }
    fn error(&self, error: KafkaError, reason: &str) {
        self.0
            .record(json!({"time":now(),"kind":"error","error":error.to_string(),"reason":reason}));
    }
}
impl ConsumerContext for LabContext {
    fn pre_rebalance(&self, _: &BaseConsumer<Self>, rebalance: &Rebalance<'_>) {
        let epoch = self.0.rebalance.fetch_add(1, Ordering::Relaxed) + 1;
        self.0.record(json!({"time":now(),"kind":"rebalance","epoch":epoch,"description":format!("{rebalance:?}")}));
    }
    fn commit_callback(&self, result: KafkaResult<()>, offsets: &TopicPartitionList) {
        self.0.record(
            json!({"time":now(),"kind":"commit","success":result.is_ok(),
            "error":result.err().map(|e|e.to_string()),"offsets":format!("{offsets:?}")}),
        );
    }
}

fn consumer(
    plan: &Plan,
    native: Arc<NativeState>,
    verify: bool,
) -> Result<BaseConsumer<LabContext>> {
    let version = rdkafka::util::get_rdkafka_version().1;
    if version != "2.12.1" {
        return Err(format!("unexpected native consumer version: {version}").into());
    }
    let mut config = ClientConfig::new();
    config
        .set("bootstrap.servers", &plan.bootstrap_servers)
        .set(
            "group.id",
            if verify {
                format!("verify-{}", plan.run_id)
            } else {
                plan.group_id.clone()
            },
        )
        .set(
            "client.id",
            format!(
                "{}-{}",
                if verify { "verifier" } else { "reader" },
                plan.run_id
            ),
        )
        .set("enable.auto.commit", "false")
        .set("enable.auto.offset.store", "false")
        .set("auto.offset.reset", "earliest")
        .set("allow.auto.create.topics", "false")
        .set("enable.partition.eof", "true")
        .set("statistics.interval.ms", "1000")
        .set("socket.timeout.ms", "10000")
        .set("max.poll.interval.ms", "300000")
        .set("queued.max.messages.kbytes", "32768");
    Ok(config.create_with_context(LabContext(native))?)
}

struct Seen {
    partition: i32,
    offset: i64,
    digest: String,
}
struct ReadOrder {
    seen: Vec<Option<Seen>>,
    last: Vec<Option<(i64, usize)>>,
    maximum: usize,
}
impl ReadOrder {
    fn new(plan: &Plan) -> Self {
        Self {
            seen: Vec::new(),
            last: vec![None; plan.partitions as usize],
            maximum: plan.minimum_messages * 4,
        }
    }
    fn observe(&mut self, row: &Observed) -> Result<bool> {
        if row.seq >= self.maximum
            || row.partition < 0
            || row.partition as usize >= self.last.len()
            || row.offset < 0
        {
            return Err("invalid observed location".into());
        }
        if self.seen.len() <= row.seq {
            self.seen.resize_with(row.seq + 1, || None);
        }
        if let Some(previous) = &self.seen[row.seq] {
            if previous.partition != row.partition
                || previous.offset != row.offset
                || previous.digest != row.sha256
            {
                return Err("same logical order changed or appears at another Kafka offset".into());
            }
            return Ok(true);
        }
        let last = &mut self.last[row.partition as usize];
        if last.is_some_and(|(offset, seq)| row.offset <= offset || row.seq <= seq) {
            return Err("per-partition producer ordering violation".into());
        }
        *last = Some((row.offset, row.seq));
        self.seen[row.seq] = Some(Seen {
            partition: row.partition,
            offset: row.offset,
            digest: row.sha256.clone(),
        });
        Ok(false)
    }
}

#[derive(Default)]
struct Counts {
    deliveries: usize,
    unique: usize,
    duplicates: usize,
    poll_errors: usize,
    idle: usize,
    probe_ok: usize,
    probe_errors: usize,
    last_message: f64,
    last_probe: f64,
}
fn progress(
    plan: &Plan,
    counts: &Counts,
    offsets: &BTreeMap<i32, i64>,
    native: &NativeState,
    stopped: bool,
) -> Value {
    json!({"time":now(),"run_id":plan.run_id,"topic":plan.topic,"deliveries":counts.deliveries,
        "unique_messages":counts.unique,"duplicates":counts.duplicates,"poll_errors":counts.poll_errors,
        "idle_polls":counts.idle,"watermark_success":counts.probe_ok,"watermark_errors":counts.probe_errors,
        "last_message_time":counts.last_message,"last_probe_time":counts.last_probe,"offsets":offsets,
        "rebalances":native.rebalance.load(Ordering::Relaxed),"stopped_by_controller":stopped,
        "native_version":rdkafka::util::get_rdkafka_version().1})
}

fn persist_offsets(
    consumer: &BaseConsumer<LabContext>,
    journal: &mut Journal,
    offsets: &BTreeMap<i32, i64>,
    topic: &str,
    native: &NativeState,
    final_commit: bool,
) -> Result<()> {
    journal.sync()?;
    if !offsets.is_empty() {
        let mut stored = TopicPartitionList::new();
        for (partition, next) in offsets {
            stored.add_partition_offset(topic, *partition, Offset::Offset(*next))?;
        }
        match consumer.store_offsets(&stored) {
            Ok(()) => {
                let mode = if final_commit {
                    CommitMode::Sync
                } else {
                    CommitMode::Async
                };
                if let Err(error) = consumer.commit(&stored, mode) {
                    native.record(json!({"time":now(),"kind":"commit_submission_error","error":error.to_string()}));
                    if final_commit {
                        return Err(error.into());
                    }
                }
            }
            Err(error) => {
                native.record(
                    json!({"time":now(),"kind":"offset_store_error","error":error.to_string()}),
                );
                if final_commit {
                    return Err(error.into());
                }
            }
        }
    }
    Ok(())
}

fn live(root: &Path, plan: &Plan, native: Arc<NativeState>) -> Result<Value> {
    let consumer = consumer(plan, native.clone(), false)?;
    consumer.subscribe(&[&plan.topic])?;
    let mut journal = Journal::new(&root.join("reader.jsonl.gz"))?;
    let mut counts = Counts::default();
    let mut offsets = BTreeMap::new();
    let mut order = ReadOrder::new(plan);
    let deadline = Instant::now() + Duration::from_secs(plan.duration_seconds);
    let mut checkpoint = Instant::now();
    let mut probe = Instant::now();
    let mut last_checkpoint_deliveries = 0;
    loop {
        native.check()?;
        if let Some(error) = consumer.client().fatal_error() {
            return Err(format!("fatal native consumer: {error:?}").into());
        }
        if Instant::now() >= deadline {
            return Err("reader safety deadline exhausted".into());
        }
        let started = Instant::now();
        match consumer.poll(Duration::from_millis(100)) {
            Some(Ok(message)) => {
                let payload = message.payload().ok_or("missing Kafka value")?;
                let mut row = match orders::decode(
                    plan,
                    payload,
                    message.key(),
                    message.partition(),
                    message.offset(),
                    started.elapsed().as_secs_f64(),
                ) {
                    Ok(row) => row,
                    Err(error) => {
                        journal.append(&json!({"stage":"wrong","time":now(),"error":error.to_string(),
                            "partition":message.partition(),"offset":message.offset(),"sha256":format!("{:x}",sha2::Sha256::digest(payload))}))?;
                        journal.finish()?;
                        return Err(error);
                    }
                };
                row.duplicate = match order.observe(&row) {
                    Ok(duplicate) => duplicate,
                    Err(error) => {
                        journal.append(&json!({"stage":"wrong","time":now(),"error":error.to_string(),"observed":row}))?;
                        journal.finish()?;
                        return Err(error);
                    }
                };
                counts.deliveries += 1;
                if row.duplicate {
                    counts.duplicates += 1;
                } else {
                    counts.unique += 1;
                }
                counts.last_message = row.time;
                offsets
                    .entry(row.partition)
                    .and_modify(|next: &mut i64| *next = (*next).max(row.offset + 1))
                    .or_insert(row.offset + 1);
                journal.append(&row)?;
            }
            Some(Err(KafkaError::PartitionEOF(partition))) => {
                journal.append(&json!({"stage":"partition_eof","time":now(),"seconds":started.elapsed().as_secs_f64(),"partition":partition}))?;
            }
            Some(Err(error)) => {
                counts.poll_errors += 1;
                journal.append(&json!({"stage":"poll_error","time":now(),"seconds":started.elapsed().as_secs_f64(),"error":error.to_string()}))?;
            }
            None => {
                counts.idle += 1;
            }
        }
        if probe.elapsed() >= Duration::from_secs(1) {
            let started = Instant::now();
            let result = consumer.fetch_watermarks(&plan.topic, 0, Duration::from_secs(1));
            let mut event = json!({"stage":"watermark","time":now(),"seconds":started.elapsed().as_secs_f64(),"partition":0});
            match result {
                Ok((low, high)) => {
                    counts.probe_ok += 1;
                    counts.last_probe = now();
                    event["low"] = json!(low);
                    event["high"] = json!(high);
                }
                Err(error) => {
                    counts.probe_errors += 1;
                    event["stage"] = json!("watermark_error");
                    event["error"] = json!(error.to_string());
                }
            }
            journal.append(&event)?;
            probe = Instant::now();
        }
        if counts.deliveries - last_checkpoint_deliveries >= 1024
            || checkpoint.elapsed() >= Duration::from_secs(1)
        {
            persist_offsets(
                &consumer,
                &mut journal,
                &offsets,
                &plan.topic,
                &native,
                false,
            )?;
            atomic_json(
                &root.join("reader-progress.json"),
                &progress(plan, &counts, &offsets, &native, false),
            )?;
            checkpoint = Instant::now();
            last_checkpoint_deliveries = counts.deliveries;
            if root.join("stop-reader").try_exists()? {
                break;
            }
            if root.join("abort-reader").try_exists()? {
                journal.finish()?;
                let mut result = progress(plan, &counts, &offsets, &native, false);
                result["aborted"] = json!(true);
                atomic_json(&root.join("reader.json"), &result)?;
                return Err("controller aborted reader".into());
            }
        }
    }
    persist_offsets(
        &consumer,
        &mut journal,
        &offsets,
        &plan.topic,
        &native,
        true,
    )?;
    journal.finish()?;
    let result = progress(plan, &counts, &offsets, &native, true);
    drop(consumer);
    native.check()?;
    atomic_json(&root.join("reader.json"), &result)?;
    Ok(result)
}

fn verify(
    root: &Path,
    output: &Path,
    plan: &Plan,
    native: Arc<NativeState>,
    diagnostic: bool,
) -> Result<Value> {
    let mut oracle = Oracle::load(root, plan)?;
    let consumer = consumer(plan, native.clone(), true)?;
    let mut assignment = TopicPartitionList::new();
    let mut ends = BTreeMap::new();
    let mut lows = BTreeMap::new();
    for partition in 0..plan.partitions {
        let (low, high) =
            consumer.fetch_watermarks(&plan.topic, partition, Duration::from_secs(10))?;
        lows.insert(partition, low);
        ends.insert(partition, high);
        assignment.add_partition_offset(&plan.topic, partition, Offset::Beginning)?;
    }
    consumer.assign(&assignment)?;
    let mut order = ReadOrder::new(plan);
    let mut errors = 0;
    let mut polls = 0;
    let mut diagnostic_order_errors = 0;
    let mut physical_hashes: Vec<sha2::Sha256> = (0..plan.partitions)
        .map(|_| sha2::Sha256::default())
        .collect();
    let mut physical_offsets = vec![-1i64; plan.partitions as usize];
    let mut native_positions = BTreeMap::new();
    let deadline = Instant::now() + Duration::from_secs(plan.duration_seconds);
    let mut checked = Instant::now();
    loop {
        native.check()?;
        if Instant::now() >= deadline {
            return Err("replay failed to reach fixed native end offsets".into());
        }
        match consumer.poll(Duration::from_millis(100)) {
            Some(Ok(message)) => {
                let payload = message.payload().ok_or("missing replayed Kafka value")?;
                let row = orders::decode(
                    plan,
                    payload,
                    message.key(),
                    message.partition(),
                    message.offset(),
                    0.,
                )?;
                if diagnostic {
                    if order.observe(&row).is_err() {
                        diagnostic_order_errors += 1;
                    }
                    oracle.diagnose(&row)?;
                    let partition = row.partition as usize;
                    if row.offset <= physical_offsets[partition] {
                        return Err("diagnostic physical offsets did not increase".into());
                    }
                    physical_offsets[partition] = row.offset;
                    physical_hashes[partition].update(row.offset.to_be_bytes());
                    physical_hashes[partition].update((row.seq as u64).to_be_bytes());
                    physical_hashes[partition].update(row.sha256.as_bytes());
                } else {
                    order.observe(&row)?;
                    oracle.observe(&row)?;
                }
                polls += 1;
            }
            Some(Err(KafkaError::PartitionEOF(_))) | None => {}
            Some(Err(error)) => {
                errors += 1;
                native
                    .record(json!({"time":now(),"kind":"replay_error","error":error.to_string()}));
            }
        }
        if checked.elapsed() >= Duration::from_secs(1) || polls % 1024 == 0 {
            let positions = consumer.position()?;
            for item in positions.elements() {
                if let Offset::Offset(offset) = item.offset() {
                    native_positions.insert(item.partition(), offset);
                }
            }
            if ends.iter().all(|(partition, end)| {
                *end == 0
                    || native_positions
                        .get(partition)
                        .is_some_and(|position| position >= end)
            }) {
                break;
            }
            atomic_json(
                &output.join("verify-progress.json"),
                &json!({"time":now(),"topic":plan.topic,
                "observed_acknowledged":oracle.observed_acknowledged,"expected_acknowledged":oracle.acknowledged,
                "positions":native_positions,"end_offsets":ends}),
            )?;
            checked = Instant::now();
        }
    }
    if !diagnostic {
        oracle.finish()?;
    }
    let partition_hashes: Vec<String> = physical_hashes
        .into_iter()
        .map(|h| format!("{:x}", h.finalize()))
        .collect();
    let result = json!({"time":now(),"run_id":plan.run_id,"topic":plan.topic,"acknowledged":oracle.acknowledged,
        "strict_verification": !diagnostic,"diagnostic_only":diagnostic,"physical_records":polls,"partition_sha256":partition_hashes,
        "duplicate_logical_records":oracle.duplicate_logical_records,"duplicate_acknowledged_records":oracle.duplicate_acknowledged_records,
        "diagnostic_order_errors":diagnostic_order_errors,
        "observed_acknowledged":oracle.observed_acknowledged,"ambiguous_intents":oracle.ambiguous,
        "observed_nonack":oracle.observed_nonack,"unique_messages":oracle.unique_messages,
        "duplicate_deliveries":oracle.duplicate_deliveries,"poll_errors":errors,"positions":native_positions,
        "start_offsets":lows,"end_offsets":ends,"acknowledged_loss":oracle.acknowledged - oracle.observed_acknowledged,"corruption":0,
        "native_version":rdkafka::util::get_rdkafka_version().1});
    drop(consumer);
    native.check()?;
    atomic_json(
        &output.join(if diagnostic {
            "diagnosis.json"
        } else {
            "verification.json"
        }),
        &result,
    )?;
    Ok(result)
}

fn run() -> Result<()> {
    let root = PathBuf::from(env::var("WITNESS_DIR").unwrap_or_else(|_| "/witness".into()));
    let mode = env::var("MODE").unwrap_or_else(|_| "live".into());
    let plan = Plan::load(&root)?;
    let output = if mode == "verify" || mode == "diagnose" {
        PathBuf::from(env::var("OUTPUT_DIR")?)
    } else {
        root.clone()
    };
    let native = Arc::new(NativeState {
        journal: Mutex::new(Journal::new(&output.join(
            if mode == "verify" || mode == "diagnose" {
                "verify-native.jsonl.gz"
            } else {
                "reader-native.jsonl.gz"
            },
        ))?),
        failed: AtomicBool::new(false),
        rebalance: AtomicU64::new(0),
        last_statistics: AtomicI64::new(0),
    });
    let result = match mode.as_str() {
        "live" => live(&root, &plan, native.clone()),
        "verify" => verify(&root, &output, &plan, native.clone(), false),
        "diagnose" => verify(&root, &output, &plan, native.clone(), true),
        _ => Err("unknown reader mode".into()),
    };
    if let Err(error) = &result {
        atomic_json(
            &output.join("failure.json"),
            &json!({"time":now(),"run_id":plan.run_id,"topic":plan.topic,"mode":mode,"error":error.to_string()}),
        )?;
    }
    native.finish()?;
    println!("{}", result?);
    Ok(())
}
fn main() {
    if env::args().len() == 2 && env::args().nth(1).as_deref() == Some("--version") {
        println!("{}", rdkafka::util::get_rdkafka_version().1);
        return;
    }
    if env::args().len() != 1 {
        eprintln!("usage: order-consumer [--version]");
        std::process::exit(1);
    }
    if let Err(error) = run() {
        eprintln!("{error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn duplicate_replay_is_distinct_from_log_reordering() -> Result<()> {
        let plan = orders::tests::plan();
        let mut tracker = ReadOrder::new(&plan);
        let first = orders::tests::row(0, 0, 0)?;
        assert!(!tracker.observe(&first)?);
        assert!(tracker.observe(&first)?);
        assert!(!tracker.observe(&orders::tests::row(2, 0, 1)?)?);
        assert!(tracker.observe(&orders::tests::row(1, 0, 2)?).is_err());
        assert!(tracker.observe(&orders::tests::row(0, 0, 3)?).is_err());
        assert!(!tracker.observe(&orders::tests::row(1, 1, 0)?)?);
        Ok(())
    }
}
