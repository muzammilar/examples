use crate::{
    actors::{append, events, save},
    metrics::{Collector, Context, disk},
    protocol::{Pair, Request, now, pair, rpc},
};
use anyhow::{Context as _, Result, ensure};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    env,
    fs::{self, File},
    io::{BufReader, Read},
    os::unix::{fs::MetadataExt, process::ExitStatusExt},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    thread,
    time::{Duration, Instant},
};
use uuid::Uuid;
const BACKENDS: [&str; 2] = ["fjall", "redb"];

struct Process(Child);
impl Drop for Process {
    fn drop(&mut self) {
        if self.0.try_wait().ok().flatten().is_none() {
            let _ = self.0.kill();
            let _ = self.0.wait();
        }
    }
}
fn launch(args: &[String], root: &Path, name: &str) -> Result<Process> {
    let logfile = File::create_new(root.join(format!("{name}-{}.log", Uuid::new_v4())))?;
    Ok(Process(
        Command::new(env::current_exe()?)
            .args(args)
            .stdout(Stdio::from(logfile.try_clone()?))
            .stderr(Stdio::from(logfile))
            .spawn()?,
    ))
}
fn owner(backend: &str, db: &Path, root: &Path, ctx: &Context) -> Result<(Process, PathBuf)> {
    let socket = PathBuf::from(format!("/tmp/catalog-{}.sock", Uuid::new_v4()));
    owner_at(backend, db, root, ctx, socket)
}
fn owner_at(
    backend: &str,
    db: &Path,
    root: &Path,
    ctx: &Context,
    socket: PathBuf,
) -> Result<(Process, PathBuf)> {
    let mut child = launch(
        &[
            "owner".into(),
            backend.into(),
            db.display().to_string(),
            socket.display().to_string(),
        ],
        root,
        "owner",
    )?;
    let deadline = Instant::now() + Duration::from_secs(60);
    loop {
        if rpc(&socket, &Request::Health).is_ok() {
            break;
        }
        ensure!(child.0.try_wait()?.is_none(), "owner exited during reopen");
        ensure!(Instant::now() < deadline, "owner recovery deadline");
        thread::sleep(Duration::from_millis(20));
    }
    ctx.processes
        .lock()
        .expect("tracker")
        .push(("owner".into(), child.0.id()));
    ctx.sample()?;
    Ok((child, socket))
}
fn stopped(child: &mut Process, socket: &Path, ctx: &Context) -> Result<()> {
    ctx.sample()?;
    rpc(socket, &Request::Stop)?;
    wait(child, 10)?;
    ctx.processes
        .lock()
        .expect("tracker")
        .retain(|(_, pid)| *pid != child.0.id());
    Ok(())
}
fn wait(child: &mut Process, seconds: u64) -> Result<()> {
    let deadline = Instant::now() + Duration::from_secs(seconds);
    loop {
        if let Some(status) = child.0.try_wait()? {
            ensure!(status.success(), "child exit {status}");
            return Ok(());
        }
        ensure!(Instant::now() < deadline, "child stop deadline");
        thread::sleep(Duration::from_millis(20));
    }
}
fn phase(ctx: &Context, journal: &mut File, name: &str) -> Result<u128> {
    ctx.sample()?;
    *ctx.phase.lock().expect("phase") = name.into();
    ctx.sample()?;
    let timestamp = now();
    append(journal, &json!({"phase":name,"timestamp_ns":timestamp}))?;
    Ok(timestamp)
}
pub(crate) fn cfg() -> Result<(Value, PathBuf, PathBuf)> {
    let lab: Value = serde_json::from_reader(File::open("/results/lab.json")?)?;
    ensure!(
        lab["state"] == "ready",
        "incomplete fixture; preserve evidence, do not reseed"
    );
    let generation = lab["generation"].as_str().context("generation")?;
    Ok((
        lab.clone(),
        PathBuf::from("/results").join(generation),
        PathBuf::from("/database").join(generation),
    ))
}
fn publish_lab(value: &Value) -> Result<()> {
    let path = PathBuf::from(format!("/results/lab-{}.json", Uuid::new_v4()));
    save(&path, value)?;
    fs::rename(path, "/results/lab.json")?;
    File::open("/results")?.sync_all()?;
    Ok(())
}
pub fn native_lock_probe(backend: &str, path: &Path) -> Result<()> {
    match crate::engine::Engine::open(backend, path) {
        Ok(_) => println!("{}", json!({"rejected":false,"backend":backend})),
        Err(error) => {
            let rejected = match backend {
                "fjall" => matches!(
                    error.downcast_ref::<fjall::Error>(),
                    Some(fjall::Error::Locked)
                ),
                "redb" => matches!(
                    error.downcast_ref::<redb::DatabaseError>(),
                    Some(redb::DatabaseError::DatabaseAlreadyOpen)
                ),
                _ => false,
            };
            ensure!(rejected, "unexpected native open failure: {error:#}");
            println!(
                "{}",
                json!({"rejected":true,"backend":backend,"native_error":format!("{error:#}")})
            );
        }
    }
    Ok(())
}
pub fn lock_probe(path: &Path) -> Result<()> {
    let file = File::options().read(true).write(true).open(path)?;
    let locked = match file.try_lock() {
        Ok(()) => true,
        Err(std::fs::TryLockError::WouldBlock) => false,
        Err(error) => return Err(error.into()),
    };
    let meta = file.metadata()?;
    println!(
        "{}",
        json!({"locked":locked,"inode":meta.ino(),"device":meta.dev()})
    );
    Ok(())
}
fn primitive_control(path: &Path) -> Result<Value> {
    let file = File::create_new(path)?;
    file.lock()?;
    let probe = || -> Result<Value> {
        let result = Command::new(env::current_exe()?)
            .arg("lock-probe")
            .arg(path)
            .output()?;
        ensure!(
            result.status.success(),
            "standard-library lock probe failed: {}",
            String::from_utf8_lossy(&result.stderr)
        );
        Ok(serde_json::from_slice(&result.stdout)?)
    };
    let rejected = probe()?;
    ensure!(
        rejected["locked"] == false
            && rejected["inode"] == file.metadata()?.ino()
            && rejected["device"] == file.metadata()?.dev(),
        "storage lock primitive failed"
    );
    file.unlock()?;
    let acquired = probe()?;
    ensure!(
        acquired["locked"] == true
            && acquired["inode"] == rejected["inode"]
            && acquired["device"] == rejected["device"],
        "released storage lock not acquired"
    );
    Ok(json!({"held":rejected,"released":acquired}))
}
fn provenance() -> Result<Value> {
    let mut source = BTreeMap::new();
    for entry in fs::read_dir("/opt/lab/src")? {
        let path = entry?.path();
        source.insert(
            path.file_name()
                .context("source name")?
                .to_string_lossy()
                .into_owned(),
            file_hash(&path)?,
        );
    }
    Ok(
        json!({"binary_sha256":file_hash(&env::current_exe()?)?,"source_sha256":source,"contract_sha256":file_hash(Path::new("/opt/lab/contract.json"))?,"cargo_lock_sha256":file_hash(Path::new("/opt/lab/Cargo.lock"))?,"kernel":String::from_utf8(Command::new("uname").arg("-r").output()?.stdout)?,"architecture":env::consts::ARCH,"clock_ticks_per_second":String::from_utf8(Command::new("getconf").arg("CLK_TCK").output()?.stdout)?.trim().parse::<u64>()?,"cpu_max":fs::read_to_string("/sys/fs/cgroup/cpu.max")?,"memory_max":fs::read_to_string("/sys/fs/cgroup/memory.max")?,"memory_swap_max":fs::read_to_string("/sys/fs/cgroup/memory.swap.max")?,"co_tenancy":"Other isolated functional Docker labs may run concurrently; timing is diagnostic, no comparative performance ranking","process_io_scope":"/proc PID io and cgroup per-device bytes/operations; not physical host IO. IO latency/queue depth unavailable. No network path: private Unix socket only."}),
    )
}
fn prepare() -> Result<()> {
    let rows: u64 = env::var("SEED_PAIRS")
        .unwrap_or_else(|_| "100000".into())
        .parse()?;
    ensure!((100..=1000000).contains(&rows), "SEED_PAIRS must be100..1m");
    if Path::new("/results/lab.json").exists() {
        let retained: Value = serde_json::from_reader(File::open("/results/lab.json")?)?;
        if retained["state"] != "destroyed" {
            ensure!(
                retained["seed_pairs"] == rows,
                "requested SEED_PAIRS={rows} differs from retained fixture {}; preserve/complete its lifecycle before choosing a new size",
                retained["seed_pairs"]
            );
            return verify();
        }
    }
    let generation = Uuid::new_v4().to_string();
    let root = PathBuf::from("/results").join(&generation);
    fs::create_dir(&root)?;
    let db = PathBuf::from("/database").join(&generation);
    fs::create_dir(&db)?;
    publish_lab(&json!({"state":"preparing","generation":generation,"seed_pairs":rows}))?;
    save(&root.join("environment.json"), &provenance()?)?;
    save(
        &root.join("primitive-lock.json"),
        &primitive_control(&db.join("primitive.lock"))?,
    )?;
    let collector = Collector::start(&root.join("setup-resources.jsonl"))?;
    let ctx = &collector.context;
    let mut phases = File::create_new(root.join("setup-phases.jsonl"))?;
    for backend in BACKENDS {
        phase(ctx, &mut phases, &format!("seed-{backend}"))?;
        let (mut child, socket) = owner(backend, &db.join(backend), &root, ctx)?;
        let mut journal = File::create_new(root.join(format!("seed-{backend}.jsonl")))?;
        for first in (0..rows).step_by(1000) {
            let end = (first + 1000).min(rows);
            let values: Vec<Pair> = (first..end).map(pair).collect();
            append(
                &mut journal,
                &json!({"kind":"seed-intent","first":first,"end":end,"timestamp_ns":now()}),
            )?;
            let start = Instant::now();
            rpc(&socket, &Request::Put { rows: values })?;
            append(
                &mut journal,
                &json!({"kind":"seed-ok","first":first,"end":end,"timestamp_ns":now(),"elapsed_ns":start.elapsed().as_nanos()}),
            )?;
        }
        controls(&socket, rows + 100)?;
        export_verify(
            &socket,
            &root.join(format!("seed-{backend}.bin")),
            &expected(&root, backend, rows)?,
        )?;
        stopped(&mut child, &socket, ctx)?;
    }
    collector.finish()?;
    publish_lab(
        &json!({"state":"ready","generation":generation,"seed_pairs":rows,"created_ns":now()}),
    )?;
    println!("Prepared {rows} pairs on both backends; exact exports verified");
    Ok(())
}
fn controls(socket: &Path, id: u64) -> Result<()> {
    rpc(socket, &Request::Snapshot { row: pair(id + 1) })?;
    rpc(socket, &Request::Abort { row: pair(id) })?;
    let absent = rpc(socket, &Request::Read { id })?;
    ensure!(
        absent.left.is_none() && absent.right.is_none(),
        "abort visible"
    );
    rpc(
        socket,
        &Request::Put {
            rows: vec![pair(id)],
        },
    )?;
    let present = rpc(socket, &Request::Read { id })?;
    ensure!(
        present.left == Some(pair(id).left) && present.right == Some(pair(id).right),
        "roundtrip"
    );
    rpc(socket, &Request::Delete { id })?;
    let deleted = rpc(socket, &Request::Read { id })?;
    ensure!(
        deleted.left.is_none() && deleted.right.is_none(),
        "delete incomplete"
    );
    Ok(())
}
#[derive(Clone)]
pub(crate) struct Expected {
    pub(crate) left: [u8; 32],
    pub(crate) right: [u8; 32],
    pub(crate) ack: bool,
}
fn hashes(pair: &Pair, ack: bool) -> Expected {
    Expected {
        left: Sha256::digest(pair.left.as_bytes()).into(),
        right: Sha256::digest(pair.right.as_bytes()).into(),
        ack,
    }
}
pub(crate) fn expected(root: &Path, backend: &str, seed: u64) -> Result<BTreeMap<u64, Expected>> {
    let mut output = BTreeMap::new();
    let records = events(&root.join(format!("seed-{backend}.jsonl")))?;
    let mut seed_count = 0;
    for e in records.iter().filter(|e| e["kind"] == "seed-ok") {
        let (first, end) = (
            e["first"].as_u64().context("seed first")?,
            e["end"].as_u64().context("seed end")?,
        );
        ensure!(first == seed_count && end <= seed, "invalid seed coverage");
        for id in first..end {
            output.insert(id, hashes(&pair(id), true));
        }
        seed_count = end;
    }
    ensure!(seed_count == seed, "incomplete seed history");
    if root.join("cases").exists() {
        for directory in fs::read_dir(root.join("cases"))? {
            let path = directory?.path();
            let meta: Value = serde_json::from_reader(File::open(path.join("case.json"))?)?;
            if meta["backend"] != backend {
                continue;
            }
            let journal = path.join("writer.jsonl");
            if !journal.exists() {
                continue;
            }
            let body = fs::read(&journal)?;
            ensure!(
                body.last() == Some(&b'\n'),
                "truncated stopped writer witness"
            );
            for e in events(&journal)? {
                let id = e["id"].as_u64().context("intent ID")?;
                if e["kind"] == "intent" {
                    let row = Pair {
                        id,
                        left: e["left"].as_str().context("left intent")?.into(),
                        right: e["right"].as_str().context("right intent")?.into(),
                    };
                    ensure!(
                        output.insert(id, hashes(&row, false)).is_none(),
                        "intent ID reused"
                    );
                } else if e["kind"] == "ok" {
                    output.get_mut(&id).context("ACK without intent")?.ack = true;
                } else {
                    ensure!(e["kind"] == "ambiguous", "unknown writer outcome");
                    ensure!(output.contains_key(&id), "outcome without intent");
                }
            }
        }
    }
    Ok(output)
}
pub(crate) fn verify_export(path: &Path, expected: &BTreeMap<u64, Expected>) -> Result<Value> {
    let mut file = BufReader::with_capacity(1024 * 1024, File::open(path)?);
    let mut seen = BTreeSet::new();
    let mut count = 0;
    let mut hash = Sha256::new();
    let mut last = [None, None];
    loop {
        let mut record = [0u8; 265];
        let read = file.read(&mut record[..1])?;
        if read == 0 {
            break;
        }
        file.read_exact(&mut record[1..])?;
        let side = record[0];
        ensure!(side < 2, "unknown side");
        let id = u64::from_be_bytes(record[1..9].try_into()?);
        ensure!(
            last[usize::from(side)].is_none_or(|previous| id > previous),
            "duplicate/unordered native key"
        );
        last[usize::from(side)] = Some(id);
        let wanted = expected.get(&id).context("unexpected native ID")?;
        let body: [u8; 32] = Sha256::digest(&record[9..]).into();
        ensure!(
            body == if side == 0 { wanted.left } else { wanted.right },
            "wrong successful body"
        );
        ensure!(seen.insert((id, side)), "duplicate native key");
        count += 1;
        hash.update(record);
    }
    let mut acks = 0;
    let mut ambiguous_applied = 0;
    let mut absent = 0;
    for (&id, value) in expected {
        let left = seen.contains(&(id, 0));
        let right = seen.contains(&(id, 1));
        ensure!(left == right, "partial atomic pair");
        if value.ack {
            acks += 1;
            ensure!(left, "acknowledged pair missing");
        } else if left {
            ambiguous_applied += 1;
        } else {
            absent += 1;
        }
    }
    Ok(
        json!({"native_records":count,"acknowledged_pairs_verified":acks,"applied_unacknowledged_intents":ambiguous_applied,"absent_unacknowledged_intents":absent,"acknowledged_loss":0,"wrong_or_partial_pairs":0,"export_bytes":fs::metadata(path)?.len(),"export_sha256":format!("{:x}",hash.finalize())}),
    )
}
fn export_verify(socket: &Path, path: &Path, expected: &BTreeMap<u64, Expected>) -> Result<Value> {
    let response = rpc(
        socket,
        &Request::Export {
            path: path.display().to_string(),
        },
    )?;
    let proof = verify_export(path, expected)?;
    ensure!(
        response.records == proof["native_records"].as_u64(),
        "export response count differs"
    );
    Ok(proof)
}
pub(crate) fn progress_counts(
    writes: &[Value],
    reads: &[Value],
    minimum: usize,
    not_before: u128,
) -> bool {
    let eligible = |event: &Value| {
        event["kind"] == "ok"
            && event["started_ns"]
                .as_u64()
                .is_some_and(|ns| u128::from(ns) >= not_before)
    };
    writes.iter().filter(|event| eligible(event)).count() >= minimum
        && ["historical", "recent"].iter().all(|locality| {
            reads
                .iter()
                .filter(|event| eligible(event) && event["locality"] == *locality)
                .count()
                >= minimum
        })
}
fn progress(
    root: &Path,
    minimum: usize,
    not_before: u128,
    children: &mut [Process],
    deadline_seconds: u64,
) -> Result<()> {
    let deadline = Instant::now() + Duration::from_secs(deadline_seconds);
    loop {
        for child in children.iter_mut() {
            ensure!(child.0.try_wait()?.is_none(), "live actor exited");
        }
        if progress_counts(
            &events(&root.join("writer.jsonl"))?,
            &events(&root.join("reader.jsonl"))?,
            minimum,
            not_before,
        ) {
            return Ok(());
        }
        ensure!(
            Instant::now() < deadline,
            "no phase-local live read/write progress before deadline"
        );
        thread::sleep(Duration::from_millis(100));
    }
}

fn file_hash(path: &Path) -> Result<String> {
    let mut file = File::open(path)?;
    let mut digest = Sha256::new();
    let mut buffer = [0u8; 65536];
    loop {
        let n = file.read(&mut buffer)?;
        if n == 0 {
            break;
        }
        digest.update(&buffer[..n]);
    }
    Ok(format!("{:x}", digest.finalize()))
}

fn copy_tree(source: &Path, destination: &Path) -> Result<()> {
    fs::create_dir(destination)?;
    for entry in fs::read_dir(source)? {
        let entry = entry?;
        let meta = entry.file_type()?;
        ensure!(!meta.is_symlink(), "refuse copied symlink");
        if meta.is_dir() {
            copy_tree(&entry.path(), &destination.join(entry.file_name()))?;
        } else {
            let target = destination.join(entry.file_name());
            fs::copy(entry.path(), &target)?;
            File::open(&target)?.sync_all()?;
            ensure!(
                file_hash(&entry.path())? == file_hash(&target)?,
                "copied native bytes differ"
            );
        }
    }
    File::open(destination)?.sync_all()?;
    Ok(())
}
fn compress(path: &Path) -> Result<Value> {
    let archive = path.with_extension("bin.gz");
    let timing = path.with_extension("gzip-time.txt");
    let start = Instant::now();
    let status = Command::new("/usr/bin/time")
        .args(["-f", "%U %S %e %M", "-o"])
        .arg(&timing)
        .args(["gzip", "-1", "-c"])
        .arg(path)
        .stdout(Stdio::from(File::create_new(&archive)?))
        .status()?;
    ensure!(status.success(), "gzip failed");
    File::open(&archive)?.sync_all()?;
    let wall = start.elapsed().as_secs_f64();
    let mut child = Command::new("gzip")
        .args(["-d", "-c"])
        .arg(&archive)
        .stdout(Stdio::piped())
        .spawn()?;
    let mut stream = child.stdout.take().context("gzip stream")?;
    let mut digest = Sha256::new();
    let mut buffer = [0u8; 65536];
    loop {
        let n = stream.read(&mut buffer)?;
        if n == 0 {
            break;
        }
        digest.update(&buffer[..n]);
    }
    ensure!(child.wait()?.success(), "gzip verification failed");
    ensure!(
        format!("{:x}", digest.finalize()) == file_hash(path)?,
        "gzip archive bytes differ"
    );
    let values: Vec<f64> = fs::read_to_string(&timing)?
        .split_whitespace()
        .map(str::parse)
        .collect::<std::result::Result<_, _>>()?;
    ensure!(values.len() == 4, "gzip process resource counters absent");
    Ok(
        json!({"codec":"gzip","level":1,"uncompressed_bytes":fs::metadata(path)?.len(),"compressed_bytes":fs::metadata(&archive)?.len(),"ratio":fs::metadata(path)?.len() as f64/fs::metadata(&archive)?.len() as f64,"wall_seconds":wall,"gzip_user_cpu_seconds":values[0],"gzip_system_cpu_seconds":values[1],"gzip_elapsed_seconds_resolution_10ms":values[2],"gzip_peak_rss_kib":values[3],"verified_decompressed_sha256":file_hash(path)?,"scope":"Off-path native full-body export compressibility; not native storage compression. Gzip CPU/RSS cover compression process and coarse10ms tool counters; controller wall includes startup/sync, excludes decompression verification."}),
    )
}
fn test() -> Result<()> {
    let (lab, root, db) = cfg()?;
    let seed = lab["seed_pairs"].as_u64().context("seed")?;
    fs::create_dir_all(root.join("cases"))?;
    for backend in BACKENDS {
        let case = root.join("cases").join(Uuid::new_v4().to_string());
        fs::create_dir(&case)?;
        save(
            &case.join("case.json"),
            &json!({"backend":backend,"generation":lab["generation"],"started_ns":now(),"contract":"single-owner-durable-atomic-pairs","provenance":provenance()?}),
        )?;
        let result = exercise(backend, &db.join(backend), &root, &case, seed);
        match result {
            Ok(report) => save(&case.join("result.json"), &report)?,
            Err(error) => {
                save(
                    &case.join("failure.json"),
                    &json!({"passed":false,"error":format!("{error:#}"),"timestamp_ns":now()}),
                )?;
                return Err(error);
            }
        }
    }
    verify()?;
    Ok(())
}
fn exercise(backend: &str, db: &Path, root: &Path, case: &Path, seed: u64) -> Result<Value> {
    let collector = Collector::start(&case.join("resources.jsonl"))?;
    let ctx = &collector.context;
    let mut phases = File::create_new(case.join("phases.jsonl"))?;
    phase(ctx, &mut phases, "retained-open")?;
    let (mut owner_process, socket) = owner(backend, db, case, ctx)?;
    export_verify(
        &socket,
        &case.join("before.bin"),
        &expected(root, backend, seed)?,
    )?;
    // Native second-owner rejection is tested in an independent process while this owner is live.
    let probe_path = case.join("native-lock-probe.json");
    let mut second = Process(
        Command::new(env::current_exe()?)
            .args(["native-lock-probe", backend, &db.display().to_string()])
            .stdout(Stdio::from(File::create_new(&probe_path)?))
            .stderr(Stdio::from(File::create_new(
                case.join("native-lock-probe-errors.log"),
            )?))
            .spawn()?,
    );
    wait(&mut second, 5)?;
    let rejection: Value = serde_json::from_reader(File::open(&probe_path)?)?;
    ensure!(
        rejection["rejected"] == true && rejection["backend"] == backend,
        "native exclusive-owner lock not enforced"
    );
    save(&case.join("lock-control.json"), &rejection)?;
    let first_id = u64::try_from(now())?;
    let mut children = Vec::new();
    for role in ["writer", "reader"] {
        let child = launch(
            &[
                role.into(),
                socket.display().to_string(),
                case.display().to_string(),
                first_id.to_string(),
            ],
            case,
            role,
        )?;
        ctx.processes
            .lock()
            .expect("tracker")
            .push((role.into(), child.0.id()));
        children.push(child);
    }
    let outcome = (|| -> Result<Value> {
        let baseline_started = phase(ctx, &mut phases, "baseline")?;
        progress(case, 20, baseline_started, &mut children, 60)?;
        thread::sleep(Duration::from_secs(5));
        let old_pid = owner_process.0.id();
        phase(ctx, &mut phases, "owner-sigkill")?;
        let fault = now();
        owner_process.0.kill()?;
        let status = owner_process.0.wait()?;
        ensure!(
            status.signal() == Some(9) && !PathBuf::from(format!("/proc/{old_pid}")).exists(),
            "SIGKILL fencing failed"
        );
        ctx.processes
            .lock()
            .expect("tracker")
            .retain(|(_, pid)| *pid != old_pid);
        save(
            &case.join("fenced.json"),
            &json!({"pid":old_pid,"signal":status.signal(),"waited":true,"proc_absent":true,"fault_ns":fault}),
        )?;
        thread::sleep(Duration::from_secs(5));
        for child in &mut children {
            ensure!(child.0.try_wait()?.is_none(), "actor stopped during outage");
        }
        phase(ctx, &mut phases, "native-recovery")?;
        let restart = now();
        let (new_owner, _) = owner_at(backend, db, case, ctx, socket.clone())?;
        owner_process = new_owner;
        ensure!(
            owner_process.0.id() != old_pid,
            "owner generation unchanged"
        );
        let recovered_started = phase(ctx, &mut phases, "recovered")?;
        progress(case, 20, recovered_started, &mut children, 60)?;
        thread::sleep(Duration::from_secs(5));
        Ok(
            json!({"fault_ns":fault,"restart_requested_ns":restart,"recovered_owner_pid":owner_process.0.id(),"old_owner_pid":old_pid,"actor_pids":children.iter().map(|p|p.0.id()).collect::<Vec<_>>()}),
        )
    })();
    File::create_new(case.join("stop-actors"))?.sync_all()?;
    for child in &mut children {
        wait(child, 10)?;
        ctx.processes
            .lock()
            .expect("tracker")
            .retain(|(_, pid)| *pid != child.0.id());
    }
    let mut report = outcome?;
    phase(ctx, &mut phases, "full-body-verification")?;
    let proof = export_verify(
        &socket,
        &case.join("after.bin"),
        &expected(root, backend, seed)?,
    )?;
    stopped(&mut owner_process, &socket, ctx)?;
    phase(ctx, &mut phases, "export-compression")?;
    let compression = compress(&case.join("after.bin"))?;
    phase(ctx, &mut phases, "fresh-copy-restore")?;
    let restore = db
        .parent()
        .context("parent")?
        .join(format!("restore-{backend}-{}", Uuid::new_v4()));
    copy_tree(db, &restore)?;
    let (mut restored, restore_socket) = owner(backend, &restore, case, ctx)?;
    let restore_proof = export_verify(
        &restore_socket,
        &case.join("restored.bin"),
        &expected(root, backend, seed)?,
    )?;
    ensure!(restore_proof == proof, "fresh-copy restore differs");
    stopped(&mut restored, &restore_socket, ctx)?;
    phase(ctx, &mut phases, "complete")?;
    collector.finish()?;
    report["finished_ns"] = json!(now());
    report["passed"] = json!(true);
    report["backend"] = json!(backend);
    report["proof"] = proof;
    report["restore_proof"] = restore_proof;
    report["export_compression"] = compression;
    report["phases"] = json!(events(&case.join("phases.jsonl"))?);
    report["disk_after"] = disk(db)?;
    report["scope"] = json!(
        "Functional process-crash/retained-state/fresh-copy recovery; one shared host, concurrent labs possible; no qualified performance ranking."
    );
    Ok(report)
}
fn verify() -> Result<()> {
    let (lab, root, db) = cfg()?;
    let seed = lab["seed_pairs"].as_u64().context("seed")?;
    let check = root.join(format!("verify-{}", Uuid::new_v4()));
    fs::create_dir(&check)?;
    let collector = Collector::start(&check.join("resources.jsonl"))?;
    let ctx = &collector.context;
    for backend in BACKENDS {
        let (mut child, socket) = owner(backend, &db.join(backend), &check, ctx)?;
        let proof = export_verify(
            &socket,
            &check.join(format!("{backend}.bin")),
            &expected(&root, backend, seed)?,
        )?;
        stopped(&mut child, &socket, ctx)?;
        save(&check.join(format!("{backend}.json")), &proof)?;
    }
    collector.finish()?;
    println!("All historical ACK bodies verified on both engines");
    Ok(())
}
fn archive() -> Result<()> {
    verify()?;
    let (lab, root, db) = cfg()?;
    let output = root.join(format!("native-state-{}", Uuid::new_v4()));
    copy_tree(&db, &output)?;
    let sum = disk(&output)?;
    save(
        &output.join("archive.json"),
        &json!({"generation":lab["generation"],"copied_after_owners_closed":true,"bytes":sum}),
    )?;
    let authorization = json!({"generation":lab["generation"],"archive":output.display().to_string(),"created_ns":now(),"byte_checked_copy":true});
    let marker = root.join(format!("teardown-authorization-{}.json", Uuid::new_v4()));
    save(&marker, &authorization)?;
    fs::copy(&marker, "/results/pending-teardown.json")?;
    File::open("/results/pending-teardown.json")?.sync_all()?;
    println!("Native stopped state archived at {}", output.display());
    Ok(())
}
pub fn record_teardown() -> Result<()> {
    let mut lab: Value = serde_json::from_reader(File::open("/results/lab.json")?)?;
    let authorization: Value =
        serde_json::from_reader(File::open("/results/pending-teardown.json")?)?;
    let inventory: Value =
        serde_json::from_reader(File::open("/results/teardown-runtime-inventory.json")?)?;
    ensure!(
        authorization["generation"] == lab["generation"]
            && authorization["byte_checked_copy"] == true,
        "teardown archive authorization differs"
    );
    ensure!(
        inventory["checked_project"] == "rust-embedded-catalog"
            && ["containers", "networks", "volumes"]
                .iter()
                .all(|name| inventory[*name].as_array().is_some_and(Vec::is_empty)),
        "owned runtime absence not proven"
    );
    let archive = PathBuf::from(authorization["archive"].as_str().context("archive path")?);
    ensure!(
        archive.starts_with(Path::new("/results")) && archive.join("archive.json").is_file(),
        "native archive missing"
    );
    lab["state"] = json!("destroyed");
    lab["destroyed_ns"] = json!(now());
    let root = PathBuf::from("/results").join(lab["generation"].as_str().context("generation")?);
    save(
        &root.join(format!("teardown-{}.json", Uuid::new_v4())),
        &json!({"authorization":authorization,"runtime_inventory":inventory,"destroyed_ns":now()}),
    )?;
    publish_lab(&lab)?;
    Ok(())
}
pub fn run(action: &str) -> Result<()> {
    fs::create_dir_all("/results")?;
    let controller_lock = File::options()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open("/database/controller.lock")?;
    controller_lock
        .try_lock()
        .context("another example controller is active; preserve its state")?;
    match action {
        "prepare" => prepare(),
        "test" => test(),
        "verify" => verify(),
        "archive" => archive(),
        _ => anyhow::bail!("unknown action"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    fn record(side: u8, row: &Pair) -> Vec<u8> {
        let mut output = vec![side];
        output.extend(row.id.to_be_bytes());
        output.extend(if side == 0 {
            row.left.as_bytes()
        } else {
            row.right.as_bytes()
        });
        output
    }
    #[test]
    fn recovered_progress_cannot_reuse_old_successes() {
        let old_write = vec![json!({"kind":"ok","started_ns":10})];
        let old_reads = vec![
            json!({"kind":"ok","started_ns":10,"locality":"historical"}),
            json!({"kind":"ok","started_ns":10,"locality":"recent"}),
        ];
        assert!(!progress_counts(&old_write, &old_reads, 1, 20));
        let new_write = vec![json!({"kind":"ok","started_ns":30})];
        assert!(!progress_counts(&new_write, &old_reads, 1, 20));
        let new_reads = vec![
            json!({"kind":"ok","started_ns":30,"locality":"historical"}),
            json!({"kind":"ok","started_ns":30,"locality":"recent"}),
        ];
        assert!(progress_counts(&new_write, &new_reads, 1, 20));
    }
    #[test]
    fn oracle_rejects_corruption_missing_partial_duplicate_and_unknown_rows() -> Result<()> {
        let folder = env::temp_dir().join(format!("catalog-oracle-{}", Uuid::new_v4()));
        fs::create_dir(&folder)?;
        let row = pair(7);
        let mut expected = BTreeMap::from([(7, hashes(&row, true))]);
        let mut good = record(0, &row);
        good.extend(record(1, &row));
        let path = folder.join("export.bin");
        fs::write(&path, &good)?;
        assert_eq!(
            verify_export(&path, &expected)?["acknowledged_pairs_verified"],
            1
        );
        let mut bad = good.clone();
        bad[40] ^= 1;
        fs::write(&path, &bad)?;
        assert!(verify_export(&path, &expected).is_err());
        fs::write(&path, [])?;
        assert!(verify_export(&path, &expected).is_err());
        expected.get_mut(&7).expect("expected").ack = false;
        fs::write(&path, record(0, &row))?;
        assert!(verify_export(&path, &expected).is_err());
        fs::write(&path, &good)?;
        assert_eq!(
            verify_export(&path, &expected)?["applied_unacknowledged_intents"],
            1
        );
        let mut duplicate = good.clone();
        duplicate.extend(record(1, &row));
        fs::write(&path, duplicate)?;
        assert!(verify_export(&path, &expected).is_err());
        fs::write(&path, record(0, &pair(8)))?;
        assert!(verify_export(&path, &expected).is_err());
        fs::remove_dir_all(folder)?;
        Ok(())
    }
    #[test]
    fn witness_checks_duplicate_intents_and_acknowledged_values() -> Result<()> {
        let folder = env::temp_dir().join(format!("catalog-witness-{}", Uuid::new_v4()));
        fs::create_dir(&folder)?;
        fs::create_dir(folder.join("cases"))?;
        let mut seed = File::create_new(folder.join("seed-fjall.jsonl"))?;
        writeln!(seed, "{{\"kind\":\"seed-ok\",\"first\":0,\"end\":1}}")?;
        let case = folder.join("cases/one");
        fs::create_dir(&case)?;
        save(&case.join("case.json"), &json!({"backend":"fjall"}))?;
        let row = pair(2);
        let intent = json!({"kind":"intent","id":2,"left":row.left,"right":row.right});
        let mut file = File::create_new(case.join("writer.jsonl"))?;
        append(&mut file, &intent)?;
        append(&mut file, &json!({"kind":"ok","id":2}))?;
        assert!(expected(&folder, "fjall", 1)?.get(&2).expect("entry").ack);
        append(&mut file, &intent)?;
        assert!(expected(&folder, "fjall", 1).is_err());
        fs::remove_dir_all(folder)?;
        Ok(())
    }
}
