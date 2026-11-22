use crate::{
    actors::{events, save},
    protocol::now,
    suite::{cfg, expected, progress_counts, verify_export},
};
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, File},
    io::Read,
    path::Path,
};
use uuid::Uuid;

fn load(path: &Path) -> Result<Value> {
    Ok(serde_json::from_reader(File::open(path)?)?)
}
fn file_hash(path: &Path) -> Result<String> {
    let mut file = File::open(path)?;
    let mut hash = Sha256::new();
    let mut buf = [0; 65536];
    loop {
        let n = file.read(&mut buf)?;
        if n == 0 {
            break;
        }
        hash.update(&buf[..n]);
    }
    Ok(format!("{:x}", hash.finalize()))
}
fn latency(records: &[&Value]) -> Value {
    let mut values: Vec<u64> = records
        .iter()
        .filter_map(|v| v["elapsed_ns"].as_u64())
        .collect();
    values.sort_unstable();
    if values.is_empty() {
        return json!({"count":0,"p50_ms":null,"p99_ms":null});
    }
    let n = values.len();
    json!({"count":n,"mean_ms":values.iter().map(|v|*v as f64).sum::<f64>()/n as f64/1e6,"p50_ms":values[(n-1)/2] as f64/1e6,"p99_ms":values[(99*n).div_ceil(100).saturating_sub(1).min(n-1)] as f64/1e6,"scope":"Per-request wall time including Unix socket, serialization, native API and full response. Writer intent fsync/setup excluded; operation rate includes pacing/witness overhead. Low-count p99 approximates maximum, not qualified tail behavior."})
}
fn counters(text: &str) -> BTreeMap<String, u64> {
    text.lines()
        .filter_map(|line| {
            let (key, value) = line.split_once(char::is_whitespace)?;
            Some((
                key.trim_end_matches(':').into(),
                value.split_whitespace().next()?.parse().ok()?,
            ))
        })
        .collect()
}
#[derive(Clone, Copy)]
struct ProcessMetrics {
    pid: u32,
    cpu: u64,
    rss: u64,
    peak_rss: u64,
}
fn process(row: &Value) -> Option<ProcessMetrics> {
    let p = &row["process"];
    let fields: Vec<&str> = p["stat"]
        .as_str()?
        .rsplit_once(") ")?
        .1
        .split_whitespace()
        .collect();
    let status = counters(p["status"].as_str()?);
    Some(ProcessMetrics {
        pid: p["pid"].as_u64()? as u32,
        cpu: fields.get(11)?.parse::<u64>().ok()? + fields.get(12)?.parse::<u64>().ok()?,
        rss: *status.get("VmRSS")? * 1024,
        peak_rss: *status.get("VmHWM")? * 1024,
    })
}

fn parse_io(sample: &Value) -> Result<BTreeMap<String, BTreeMap<String, u64>>> {
    let Some(text) = sample["container_cgroup"]["io.stat"].as_str() else {
        return Ok(BTreeMap::new());
    };
    let mut output = BTreeMap::new();
    for line in text.lines() {
        let mut parts = line.split_whitespace();
        let device = parts.next().context("IO device")?;
        let mut values = BTreeMap::new();
        for field in parts {
            let (key, value) = field.split_once('=').context("IO counter format")?;
            ensure!(
                values.insert(key.into(), value.parse()?).is_none(),
                "duplicate IO counter"
            );
        }
        // Linux6.12 blkcg_print_one_stat omits all four fields only when all are zero.
        if values.is_empty() {
            for key in ["rbytes", "wbytes", "rios", "wios"] {
                values.insert(key.into(), 0);
            }
        }
        ensure!(
            ["rbytes", "wbytes", "rios", "wios"]
                .iter()
                .all(|key| values.contains_key(*key)),
            "partial native IO counters"
        );
        ensure!(
            output.insert(device.into(), values).is_none(),
            "duplicate IO device"
        );
    }
    Ok(output)
}
fn verify_reads(reads: &[Value], wanted: &BTreeMap<u64, crate::suite::Expected>) -> Result<usize> {
    let mut count = 0;
    for event in reads {
        ensure!(event["kind"] != "mismatch", "read mismatch");
        if event["kind"] == "ok" {
            let body = wanted
                .get(&event["id"].as_u64().context("read ID")?)
                .context("read ID not in witness")?;
            ensure!(body.ack, "recent/historical reader used unacknowledged ID");
            ensure!(
                event["actual_left_sha256"]
                    == format!("{:x}", sha2::digest::Output::<Sha256>::from(body.left))
                    && event["actual_right_sha256"]
                        == format!("{:x}", sha2::digest::Output::<Sha256>::from(body.right)),
                "read body hash differs"
            );
            count += 1;
        }
    }
    Ok(count)
}
fn summarize_resources(samples: &[Value], ticks: u64) -> Result<Value> {
    ensure!(samples.len() >= 2, "insufficient resource samples");
    let phases: BTreeSet<String> = samples
        .iter()
        .filter_map(|s| s["phase"].as_str().map(str::to_owned))
        .collect();
    let mut output = BTreeMap::new();
    for phase in phases {
        let selected: Vec<&Value> = samples.iter().filter(|s| s["phase"] == phase).collect();
        let roles: BTreeSet<String> = selected
            .iter()
            .flat_map(|s| {
                s["roles"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(|r| r["role"].as_str().map(str::to_owned))
            })
            .collect();
        let mut per_role = BTreeMap::new();
        for role in roles {
            let observations: Vec<(&Value, ProcessMetrics)> = selected
                .iter()
                .filter_map(|s| {
                    s["roles"]
                        .as_array()?
                        .iter()
                        .find(|r| r["role"] == role)
                        .and_then(process)
                        .map(|p| (*s, p))
                })
                .collect();
            let mut cpu = 0.0;
            let mut duration = 0.0;
            let mut peak_percent: f64 = 0.0;
            for window in observations.windows(2) {
                let ((a, before), (b, after)) = (window[0], window[1]);
                if before.pid == after.pid && after.cpu >= before.cpu {
                    let seconds = (b["timestamp_ns"].as_u64().context("sample time")?
                        - a["timestamp_ns"].as_u64().context("sample time")?)
                        as f64
                        / 1e9;
                    if seconds > 0.0 {
                        let delta = (after.cpu - before.cpu) as f64 / ticks as f64;
                        cpu += delta;
                        duration += seconds;
                        peak_percent = peak_percent.max(delta / seconds * 100.0);
                    }
                }
            }
            let memory: Vec<u64> = observations.iter().map(|(_, p)| p.rss).collect();
            per_role.insert(role,json!({"usable_samples":observations.len(),"observed_cpu_core_seconds":if duration>0.0{Some(cpu)}else{None},"observed_cpu_seconds":duration,"average_cpu_percent_one_core_100":if duration>0.0{Some(cpu/duration*100.0)}else{None},"peak_sampled_cpu_percent_one_core_100":if duration>0.0{Some(peak_percent)}else{None},"sampled_mean_rss_bytes":if memory.is_empty(){None}else{Some(memory.iter().map(|v|*v as f64).sum::<f64>()/memory.len() as f64)},"sampled_peak_rss_bytes":memory.iter().max(),"native_lifetime_peak_rss_bytes":observations.iter().map(|(_,p)|p.peak_rss).max()}));
        }
        let memory: Vec<u64> = selected
            .iter()
            .filter_map(|s| {
                s["container_cgroup"]["memory.current"]
                    .as_str()?
                    .trim()
                    .parse()
                    .ok()
            })
            .collect();
        let disk: Vec<&Value> = selected
            .iter()
            .filter(|s| s["database_storage"]["available"] == true)
            .map(|s| &s["database_storage"])
            .collect();
        let mut io: BTreeMap<String, BTreeMap<String, u64>> = BTreeMap::new();
        let mut io_seconds = 0.0;
        let mut io_device_seconds: BTreeMap<String, f64> = BTreeMap::new();
        let mut counter_resets = 0u64;
        let mut container_cpu = 0.0;
        let mut container_cpu_seconds = 0.0;
        for window in selected.windows(2) {
            let before = window[0]["container_cgroup"]["cpu.stat"]
                .as_str()
                .map(counters);
            let after = window[1]["container_cgroup"]["cpu.stat"]
                .as_str()
                .map(counters);
            if let (Some(before), Some(after)) = (before, after)
                && let (Some(a), Some(b)) = (before.get("usage_usec"), after.get("usage_usec"))
                && b >= a
            {
                container_cpu += (b - a) as f64 / 1e6;
                container_cpu_seconds +=
                    (window[1]["timestamp_ns"].as_u64().context("sample time")?
                        - window[0]["timestamp_ns"].as_u64().context("sample time")?)
                        as f64
                        / 1e9;
            }
        }
        for window in selected.windows(2) {
            let (a, b) = (parse_io(window[0])?, parse_io(window[1])?);
            if a.is_empty() || b.is_empty() {
                continue;
            }
            let seconds = (window[1]["timestamp_ns"].as_u64().context("sample time")?
                - window[0]["timestamp_ns"].as_u64().context("sample time")?)
                as f64
                / 1e9;
            io_seconds += seconds;
            for (device, after) in b {
                if let Some(before) = a.get(&device) {
                    if after.iter().any(|(field, value)| {
                        before.get(field).is_some_and(|previous| value < previous)
                    }) {
                        counter_resets += 1;
                        continue;
                    }
                    *io_device_seconds.entry(device.clone()).or_default() += seconds;
                    for (field, value) in after {
                        if let Some(previous) = before.get(&field)
                            && value >= *previous
                        {
                            *io.entry(device.clone())
                                .or_default()
                                .entry(field)
                                .or_default() += value - previous;
                        }
                    }
                }
            }
        }
        let io_rates: BTreeMap<String, Value> = io
            .into_iter()
            .map(|(device, values)| {
                let rates: BTreeMap<String, f64> = values
                    .iter()
                    .map(|(k, v)| {
                        (
                            format!("{k}_per_second"),
                            *v as f64 / io_device_seconds[&device],
                        )
                    })
                    .collect();
                let seconds = io_device_seconds[&device];
                (
                    device,
                    json!({"observed_delta":values,"rates":rates,"observed_seconds":seconds}),
                )
            })
            .collect();
        output.insert(phase,json!({"samples":selected.len(),"roles":per_role,"container_mean_charged_memory_bytes":if memory.is_empty(){None}else{Some(memory.iter().map(|v|*v as f64).sum::<f64>()/memory.len() as f64)},"container_peak_sampled_charged_memory_bytes":memory.iter().max(),"container_lifetime_peak_charged_memory_bytes":selected.iter().filter_map(|s|s["container_cgroup"]["memory.peak"].as_str()?.trim().parse::<u64>().ok()).max(),"database_disk_usable_samples":disk.len(),"database_disk_incomplete_samples":selected.len()-disk.len(),"database_logical_bytes_min":disk.iter().filter_map(|d|d["logical_bytes"].as_u64()).min(),"database_logical_bytes_max":disk.iter().filter_map(|d|d["logical_bytes"].as_u64()).max(),"database_allocated_bytes_min":disk.iter().filter_map(|d|d["allocated_bytes"].as_u64()).min(),"database_allocated_bytes_max":disk.iter().filter_map(|d|d["allocated_bytes"].as_u64()).max(),"whole_container_observed_cpu_core_seconds":if container_cpu_seconds>0.0{Some(container_cpu)}else{None},"whole_container_average_cpu_percent_one_core_100":if container_cpu_seconds>0.0{Some(container_cpu/container_cpu_seconds*100.0)}else{None},"io_counter_resets_excluded":counter_resets,"whole_container_per_device_io":io_rates,"whole_container_io_observed_seconds":io_seconds,"io_availability":if io_seconds>0.0{"available"}else{"unavailable: kernel exported no usable per-device intervals"}}));
    }
    Ok(
        json!({"phases":output,"scope":"Approx1s plus phase-boundary samples. CPU uses same-PID deltas; reset/edge intervals excluded. Owner totals include all native threads. RSS includes process mappings; cgroup memory also charges cache/kernel and all actors. Native lifetime peaks span earlier phases. Per-device cgroup IO includes writers/readers/controller/export/compression; do not add stacked devices or /proc byte counters. Physical host IO latency/queue depth unavailable. Database space includes both backends plus retained restore copies, not one table's compression ratio."}),
    )
}

pub fn run() -> Result<()> {
    let (lab, root, _) = cfg()?;
    let seed = lab["seed_pairs"].as_u64().context("seed count")?;
    let current_binary = file_hash(&std::env::current_exe()?)?;
    let mut cells = Vec::new();
    for backend in ["fjall", "redb"] {
        let mut cases = Vec::new();
        for entry in fs::read_dir(root.join("cases"))? {
            let path = entry?.path();
            let meta = load(&path.join("case.json"))?;
            if meta["backend"] == backend && meta["provenance"]["binary_sha256"] == current_binary {
                ensure!(
                    !path.join("failure.json").exists(),
                    "current-source failed case remains"
                );
                ensure!(
                    path.join("result.json").exists(),
                    "current-source incomplete case remains"
                );
                cases.push((meta, path));
            }
        }
        cases.sort_by_key(|(meta, _)| meta["started_ns"].as_u64());
        let (meta, path) = cases
            .last()
            .context("no executed case for current source")?;
        let result = load(&path.join("result.json"))?;
        ensure!(result["passed"] == true, "failed native case");
        let wanted = expected(&root, backend, seed)?;
        let proof = verify_export(&path.join("after.bin"), &wanted)?;
        ensure!(
            proof == result["proof"]
                && verify_export(&path.join("restored.bin"), &wanted)? == proof,
            "saved proof differs"
        );
        let writes = events(&path.join("writer.jsonl"))?;
        let reads = events(&path.join("reader.jsonl"))?;
        let read_successes = verify_reads(&reads, &wanted)?;
        let phases = events(&path.join("phases.jsonl"))?;
        let mut operation_phases = BTreeMap::new();
        for (index, phase) in phases.iter().enumerate() {
            let start = phase["timestamp_ns"].as_u64().context("phase start")?;
            let end = phases
                .get(index + 1)
                .and_then(|p| p["timestamp_ns"].as_u64())
                .unwrap_or_else(|| result["finished_ns"].as_u64().unwrap_or(0));
            ensure!(end >= start, "case end/phase timestamp missing or reversed");
            let name = phase["phase"].as_str().context("phase name")?;
            let selected_writes: Vec<Value> = writes
                .iter()
                .filter(|e| {
                    e["started_ns"]
                        .as_u64()
                        .is_some_and(|t| t >= start && t < end)
                })
                .cloned()
                .collect();
            let selected_reads: Vec<Value> = reads
                .iter()
                .filter(|e| {
                    e["started_ns"]
                        .as_u64()
                        .is_some_and(|t| t >= start && t < end)
                })
                .cloned()
                .collect();
            if name == "baseline" || name == "recovered" {
                ensure!(
                    progress_counts(&selected_writes, &selected_reads, 20, u128::from(start)),
                    "missing phase-local read/write progress"
                );
            }
            if name == "owner-sigkill" {
                ensure!(
                    selected_writes.iter().any(|e| e["kind"] == "ambiguous")
                        && selected_reads.iter().any(|e| e["kind"] == "fail"),
                    "fault did not produce live read/write error observations"
                );
            }
            let mut operations = BTreeMap::new();
            let historical: Vec<Value> = selected_reads
                .iter()
                .filter(|e| e["locality"] == "historical")
                .cloned()
                .collect();
            let recent: Vec<Value> = selected_reads
                .iter()
                .filter(|e| e["locality"] == "recent")
                .cloned()
                .collect();
            for (role, records) in [
                ("writer", &selected_writes),
                ("reader", &selected_reads),
                ("reader_historical", &historical),
                ("reader_recent", &recent),
            ] {
                let mut outcomes = BTreeMap::new();
                for kind in ["ok", "fail", "ambiguous", "mismatch"] {
                    let selected: Vec<&Value> =
                        records.iter().filter(|e| e["kind"] == kind).collect();
                    let mut values = latency(&selected);
                    values["rate_per_second"] =
                        json!(selected.len() as f64 / ((end - start) as f64 / 1e9));
                    outcomes.insert(kind, values);
                }
                operations.insert(role, outcomes);
            }
            operation_phases.insert(name,json!({"started_ns":start,"ended_ns":end,"seconds":(end-start)as f64/1e9,"operations":operations}));
        }
        let fault = result["fault_ns"].as_u64().context("fault time")?;
        let first_success = |records: &[Value]| {
            records
                .iter()
                .filter(|e| {
                    e["kind"] == "ok" && e["started_ns"].as_u64().is_some_and(|ns| ns >= fault)
                })
                .filter_map(|e| e["ended_ns"].as_u64())
                .min()
                .map(|t| (t - fault) as f64 / 1e9)
        };
        let samples = events(&path.join("resources.jsonl"))?;
        let ticks = meta["provenance"]["clock_ticks_per_second"]
            .as_u64()
            .context("ticks")?;
        cells.push(json!({"backend":backend,"case_id":path.file_name().context("case ID")?.to_string_lossy(),"provenance":meta["provenance"],"proof":proof,"read_successes_checked":read_successes,"first_successful_write_after_fault_seconds":first_success(&writes),"first_successful_read_after_fault_seconds":first_success(&reads),"operation_phases":operation_phases,"resources":summarize_resources(&samples,ticks)?,"export_compression":result["export_compression"],"disk_after":result["disk_after"],"raw_result_sha256":file_hash(&path.join("result.json"))?}));
    }
    let mut output = json!({"passed":true,"created_ns":now(),"generation":lab["generation"],"seed_pairs_per_backend":seed,"payload_bytes_per_value":256,"values_per_pair":2,"initial_logical_key_and_value_bytes_per_backend":seed*2*(8+256),"cells":cells,"scope":"Functional process-crash, atomic-pair, retained reopen and fresh-copy restore qualification; one observation per backend, concurrent isolated labs on one Docker host. No capacity, HA, physical power-loss guarantee or comparative performance winner. Cold/warm cache isolation, sustained matrix, disk loss/EIO/full disk, compaction stalls, upgrades/migration and live backup remain unrun."});
    if let Ok(header) = fs::read_to_string("/results/host-header.txt") {
        output["host_header"] = json!(header);
    }
    let path = root.join(format!("published-{}.json", Uuid::new_v4()));
    save(&path, &output)?;
    fs::copy(path, "/results/summary.json")?;
    File::open("/results/summary.json")?.sync_all()?;
    println!("Qualified functional summaries published to /results/summary.json");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn bare_device_is_zero_but_absent_device_is_unknown() -> Result<()> {
        assert!(parse_io(&json!({"container_cgroup":{"io.stat":""}}))?.is_empty());
        assert_eq!(
            parse_io(&json!({"container_cgroup":{"io.stat":"254:0\n"}}))?["254:0"]["wios"],
            0
        );
        assert!(parse_io(&json!({"container_cgroup":{"io.stat":"254:0 rios=2\n"}})).is_err());
        assert!(
            parse_io(
                &json!({"container_cgroup":{"io.stat":"254:0 rbytes=-1 wbytes=0 rios=0 wios=0\n"}})
            )
            .is_err()
        );
        Ok(())
    }
    #[test]
    fn offline_read_checker_rejects_forged_success_hash() -> Result<()> {
        let row = crate::protocol::pair(0);
        let left: [u8; 32] = Sha256::digest(row.left.as_bytes()).into();
        let right: [u8; 32] = Sha256::digest(row.right.as_bytes()).into();
        let wanted = BTreeMap::from([(
            0,
            crate::suite::Expected {
                left,
                right,
                ack: true,
            },
        )]);
        let mut event = json!({"kind":"ok","id":0,"actual_left_sha256":format!("{:x}",sha2::digest::Output::<Sha256>::from(left)),"actual_right_sha256":format!("{:x}",sha2::digest::Output::<Sha256>::from(right))});
        assert_eq!(verify_reads(&[event.clone()], &wanted)?, 1);
        event["actual_left_sha256"] = json!("wrong");
        assert!(verify_reads(&[event], &wanted).is_err());
        Ok(())
    }
}
