//! IoT fleet showcase for InfluxDB 3 Core over plain HTTP (no official Rust client):
//! 1. ingest at rising series cardinality (no series index, so no cardinality limit),
//! 2. dashboard queries: last value / distinct value caches against the same answer in SQL,
//! 3. a week of history persisted as Parquet in the object store, recent vs old data, and the
//!    query file limit that bounds how much of it one query can read in Core.

use std::env;
use std::error::Error;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde_json::{Value, json};

type R<T> = Result<T, Box<dyn Error + Send + Sync>>;

const NS: i64 = 1_000_000_000;

fn env_or<T: std::str::FromStr>(key: &str, default: T) -> T {
    env::var(key).ok().and_then(|v| v.parse().ok()).unwrap_or(default)
}

fn now_ns() -> i64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos() as i64
}

/// Tiny deterministic PRNG (xorshift64*), so runs write the same values.
struct Rng(u64);
impl Rng {
    fn next(&mut self) -> u64 {
        self.0 ^= self.0 >> 12;
        self.0 ^= self.0 << 25;
        self.0 ^= self.0 >> 27;
        self.0.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }
    fn f(&mut self) -> f64 {
        (self.next() >> 11) as f64 / (1u64 << 53) as f64
    }
}

struct Influx {
    http: reqwest::Client,
    base: String,
    token: String,
    db: String,
}

impl Influx {
    async fn call(&self, method: reqwest::Method, path: &str, body: Option<Value>) -> R<String> {
        let mut req = self.http.request(method, format!("{}{}", self.base, path)).bearer_auth(&self.token);
        if let Some(b) = body {
            req = req.json(&b);
        }
        let resp = req.send().await?;
        let status = resp.status();
        let text = resp.text().await?;
        if !status.is_success() {
            return Err(format!("{path} -> {status}: {}", text.chars().take(600).collect::<String>()).into());
        }
        Ok(text)
    }

    async fn write(&self, lines: String, no_sync: bool) -> R<()> {
        let path = format!("/api/v3/write_lp?db={}&precision=nanosecond{}", self.db, if no_sync { "&no_sync=true" } else { "" });
        let resp = self.http.post(format!("{}{}", self.base, path)).bearer_auth(&self.token).body(lines).send().await?;
        if !resp.status().is_success() {
            let s = resp.status();
            return Err(format!("write -> {s}: {}", resp.text().await.unwrap_or_default()).into());
        }
        Ok(())
    }

    async fn sql(&self, q: &str) -> R<Vec<Value>> {
        let text = self
            .call(reqwest::Method::POST, "/api/v3/query_sql", Some(json!({"db": self.db, "q": q, "format": "json"})))
            .await?;
        Ok(serde_json::from_str::<Value>(&text)?.as_array().cloned().unwrap_or_default())
    }

    async fn configure(&self, what: &str, body: Value) -> R<()> {
        self.call(reqwest::Method::POST, &format!("/api/v3/configure/{what}"), Some(body)).await.map(|_| ())
    }

    /// Server resident memory from /metrics (jemalloc), if exposed.
    async fn resident_mib(&self) -> Option<f64> {
        let text = self.call(reqwest::Method::GET, "/metrics", None).await.ok()?;
        text.lines()
            .find(|l| l.starts_with("jemalloc_memstats_bytes{stat=\"resident\"}"))
            .and_then(|l| l.split_whitespace().last())
            .and_then(|v| v.parse::<f64>().ok())
            .map(|b| b / 1048576.0)
    }
}

fn pct(sorted: &[f64], p: f64) -> f64 {
    if sorted.is_empty() {
        return 0.0;
    }
    sorted[((p / 100.0) * (sorted.len() - 1) as f64).round() as usize]
}

fn sorted(mut v: Vec<f64>) -> Vec<f64> {
    v.sort_by(|a, b| a.partial_cmp(b).unwrap());
    v
}

struct Ingest {
    rows: usize,
    bytes: usize,
    secs: f64,
    p50_ms: f64,
}

/// Posts the batches in order from `writers` concurrent tasks (keep-alive connections).
async fn ingest(db: Arc<Influx>, batches: Vec<String>, rows: usize, writers: usize, no_sync: bool) -> R<Ingest> {
    let bytes = batches.iter().map(String::len).sum();
    let mut rev = batches;
    rev.reverse(); // pop() hands them out oldest first
    let queue = Arc::new(Mutex::new(rev));
    let start = Instant::now();
    let mut tasks = Vec::new();
    for _ in 0..writers {
        let (db, queue) = (db.clone(), queue.clone());
        tasks.push(tokio::spawn(async move {
            let mut lat = Vec::new();
            loop {
                let Some(body) = queue.lock().unwrap().pop() else { break };
                let t = Instant::now();
                db.write(body, no_sync).await?;
                lat.push(t.elapsed().as_secs_f64() * 1000.0);
            }
            Ok::<_, Box<dyn Error + Send + Sync>>(lat)
        }));
    }
    let mut lat = Vec::new();
    for t in tasks {
        lat.extend(t.await??);
    }
    let secs = start.elapsed().as_secs_f64();
    let lat = sorted(lat);
    Ok(Ingest { rows, bytes, secs, p50_ms: pct(&lat, 50.0) })
}

/// Line protocol for `devices` devices x `points` points each, `step_ns` apart, ending at `end`,
/// in time order, `batch` lines per request.
fn sensor_lines(table: &str, devices: usize, sites: usize, points: usize, step_ns: i64, end: i64, batch: usize) -> Vec<String> {
    let mut rng = Rng(0x9E37_79B9_7F4A_7C15 ^ devices as u64);
    let mut out = Vec::new();
    let mut cur = String::with_capacity(batch * 120);
    let mut n = 0;
    let tags: Vec<String> = (0..devices)
        .map(|d| format!("{table},site=site-{:04},device_id=dev-{d:07},model=m{}", d % sites, d % 8))
        .collect();
    for p in 0..points {
        let ts = end - (points - 1 - p) as i64 * step_ns;
        let hour = ((ts / NS / 3600) % 24) as f64;
        for (d, tag) in tags.iter().enumerate() {
            let temp = 18.0 + (d % 7) as f64 + 4.0 * (hour / 24.0 * std::f64::consts::TAU).sin() + rng.f();
            let battery = 100 - ((d as u64 * 7 + p as u64 + rng.next() % 3) % 100);
            let rssi = -40 - (rng.next() % 50) as i64;
            cur.push_str(&format!(
                "{tag} temp={temp:.2},humidity={:.1},battery={battery}i,rssi={rssi}i {ts}\n",
                30.0 + 20.0 * rng.f()
            ));
            n += 1;
            if n % batch == 0 {
                out.push(std::mem::replace(&mut cur, String::with_capacity(batch * 120)));
            }
        }
    }
    if !cur.is_empty() {
        out.push(cur);
    }
    out
}

fn short(n: usize) -> String {
    match n {
        n if n >= 1_000_000 && n % 1_000_000 == 0 => format!("{}m", n / 1_000_000),
        n if n >= 1000 && n % 1000 == 0 => format!("{}k", n / 1000),
        n => n.to_string(),
    }
}

fn thousands(n: f64) -> String {
    let s = format!("{:.0}", n);
    let mut out = String::new();
    for (i, c) in s.chars().enumerate() {
        if i > 0 && (s.len() - i) % 3 == 0 && c != '-' {
            out.push(',');
        }
        out.push(c);
    }
    out
}

/// Runs `q` once (cold) and then `iter` times; returns (rows, first ms, p50 ms, p99 ms).
async fn timed(db: &Influx, q: &str, iter: usize) -> R<(usize, f64, f64, f64)> {
    let t = Instant::now();
    let rows = db.sql(q).await?.len();
    let first = t.elapsed().as_secs_f64() * 1000.0;
    let mut v = Vec::new();
    for _ in 0..iter {
        let t = Instant::now();
        db.sql(q).await?;
        v.push(t.elapsed().as_secs_f64() * 1000.0);
    }
    let v = sorted(v);
    Ok((rows, first, pct(&v, 50.0), pct(&v, 99.0)))
}

#[tokio::main]
async fn main() -> R<()> {
    let base = env::var("INFLUX_URL").unwrap_or_else(|_| "http://influxdb3:8181".into());
    let token_file = env::var("TOKEN_FILE").unwrap_or_else(|_| "/token/admin.json".into());
    let token = serde_json::from_str::<Value>(&std::fs::read_to_string(&token_file)?)?["token"]
        .as_str()
        .ok_or("no token in token file")?
        .to_string();
    let levels: Vec<usize> = env::var("CARDINALITIES")
        .unwrap_or_else(|_| "1000,10000,100000,1000000".into())
        .split(',')
        .filter_map(|s| s.trim().parse().ok())
        .collect();
    let rows_per_level: usize = env_or("ROWS_PER_LEVEL", 1_000_000);
    let writers: usize = env_or("WRITERS", 4);
    let batch: usize = env_or("BATCH", 10_000);
    let fleet_devices: usize = env_or("FLEET_DEVICES", 100_000);
    let fleet_points: usize = env_or("FLEET_POINTS", 10);
    let hist_devices: usize = env_or("HISTORY_DEVICES", 1000);
    let hist_days: usize = env_or("HISTORY_DAYS", 7);
    let hist_step_s: i64 = env_or("HISTORY_STEP_S", 300);
    let iter: usize = env_or("ITER", 30);

    let db = Arc::new(Influx {
        http: reqwest::Client::builder().timeout(Duration::from_secs(300)).build()?,
        base,
        token,
        db: env::var("DATABASE").unwrap_or_else(|_| "iot".into()),
    });
    let ping: Value = serde_json::from_str(&db.call(reqwest::Method::GET, "/ping", None).await?)?;
    println!("{} {} at {}, database {}", ping["product_name"].as_str().unwrap_or("?"), ping["version"].as_str().unwrap_or("?"), db.base, db.db);

    // fresh database every run
    let _ = db.call(reqwest::Method::DELETE, &format!("/api/v3/configure/database?db={}&hard_delete_at=now", db.db), None).await;
    db.configure("database", json!({"db": db.db})).await?;

    // ---- 1. cardinality ladder
    println!("\n1. ingest vs series cardinality: {} rows per level, {writers} writers, {} lines per request", thousands(rows_per_level as f64), thousands(batch as f64));
    println!("   durable = ack after the WAL flush to the object store; no_sync = ack before it (then wait until all rows are queryable)");
    println!("   {:>9} {:>11} {:>13} {:>8} {:>13} {:>10} {:>8}  {}", "series", "rows", "durable r/s", "p50 ms", "no_sync r/s", "queryable", "heap MiB", "check");
    for &series in &levels {
        let points = (rows_per_level / series).max(1);
        let mut out = Vec::new();
        for no_sync in [false, true] {
            let table = format!("ladder_{}{}", short(series), if no_sync { "_ns" } else { "" });
            let lines = sensor_lines(&table, series, 1000.min(series), points, NS, now_ns(), batch);
            let r = ingest(db.clone(), lines, series * points, writers, no_sync).await?;
            let t = Instant::now();
            let (mut n, mut s);
            loop {
                let check = db.sql(&format!("SELECT count(*) AS n, count(DISTINCT device_id) AS s FROM {table}")).await?;
                (n, s) = (check[0]["n"].as_u64().unwrap_or(0) as usize, check[0]["s"].as_u64().unwrap_or(0) as usize);
                if n >= r.rows || t.elapsed() > Duration::from_secs(60) {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(50)).await;
            }
            if n != r.rows || s != series {
                return Err(format!("{table}: {n} rows / {s} series, expected {} / {series}", r.rows).into());
            }
            out.push((r, t.elapsed().as_secs_f64(), n, s));
        }
        let rss = db.resident_mib().await.map(|m| format!("{m:.0}")).unwrap_or_else(|| "-".into());
        let (d, ns) = (&out[0], &out[1]);
        println!(
            "   {:>9} {:>11} {:>13} {:>8.0} {:>13} {:>9.2}s {:>8}  {} rows, {} distinct device_id in each table",
            thousands(series as f64), thousands(d.0.rows as f64), thousands(d.0.rows as f64 / d.0.secs), d.0.p50_ms,
            thousands(ns.0.rows as f64 / ns.0.secs), ns.1, rss, thousands(d.2 as f64), thousands(d.3 as f64)
        );
    }

    // ---- 2. dashboard: caches vs SQL
    let sites = (fleet_devices / 100).max(1);
    println!("\n2. dashboard on `fleet`: {} devices in {} sites, {} readings each; last value cache keyed (site, device_id)", thousands(fleet_devices as f64), thousands(sites as f64), fleet_points);
    db.configure("table", json!({
        "db": db.db, "table": "fleet", "tags": ["site", "device_id", "model"],
        "fields": [{"name": "temp", "type": "float64"}, {"name": "humidity", "type": "float64"},
                   {"name": "battery", "type": "int64"}, {"name": "rssi", "type": "int64"}]
    })).await?;
    db.configure("last_cache", json!({"db": db.db, "table": "fleet", "name": "fleet_last",
        "key_columns": ["site", "device_id"], "count": 1, "ttl": 3600})).await?;
    db.configure("distinct_cache", json!({"db": db.db, "table": "fleet", "name": "fleet_sites", "columns": ["site"]})).await?;
    let lines = sensor_lines("fleet", fleet_devices, sites, fleet_points, 10 * NS, now_ns(), batch);
    let r = ingest(db.clone(), lines, fleet_devices * fleet_points, writers, false).await?;
    println!("   wrote {} rows in {:.2} s = {} rows/s (the caches fill on the write path)", thousands(r.rows as f64), r.secs, thousands(r.rows as f64 / r.secs));
    let dev = fleet_devices / 2 + 21;
    let (site, device) = (format!("site-{:04}", dev % sites), format!("dev-{dev:07}"));
    let lvc = "last_cache('fleet', 'fleet_last')";
    let pairs = [
        ("1 device, latest reading",
         format!("SELECT * FROM fleet WHERE site = '{site}' AND device_id = '{device}' ORDER BY time DESC LIMIT 1"),
         format!("SELECT * FROM {lvc} WHERE site = '{site}' AND device_id = '{device}'")),
        ("1 site, latest per device",
         format!("SELECT device_id, max(time) AS time, last_value(temp ORDER BY time) AS temp, last_value(battery ORDER BY time) AS battery FROM fleet WHERE site = '{site}' GROUP BY device_id"),
         format!("SELECT device_id, time, temp, battery FROM {lvc} WHERE site = '{site}'")),
        ("whole fleet: battery < 20 now",
         "SELECT count(*) AS n FROM (SELECT device_id, last_value(battery ORDER BY time) AS b FROM fleet GROUP BY device_id) WHERE b < 20".to_string(),
         format!("SELECT count(*) AS n FROM {lvc} WHERE battery < 20")),
        ("list of sites",
         "SELECT DISTINCT site FROM fleet".to_string(),
         "SELECT site FROM distinct_cache('fleet', 'fleet_sites')".to_string()),
    ];
    println!("   {:<31} {:>6} {:>10} {:>10} {:>10} {:>10} {:>8}  {}", "query", "rows", "SQL p50", "SQL p99", "cache p50", "cache p99", "speedup", "same answer");
    for (label, plain, cached) in &pairs {
        let a = timed(&db, plain, iter).await?;
        let b = timed(&db, cached, iter).await?;
        let mut ra = db.sql(plain).await?.iter().map(Value::to_string).collect::<Vec<_>>();
        let mut rb = db.sql(cached).await?.iter().map(Value::to_string).collect::<Vec<_>>();
        ra.sort();
        rb.sort();
        let same = if label.starts_with("1 device") { a.0 == b.0 } else { ra == rb };
        println!("   {label:<31} {:>6} {:>8.2}ms {:>8.2}ms {:>8.2}ms {:>8.2}ms {:>7.1}x  {}", a.0, a.2, a.3, b.2, b.3, a.2 / b.2, if same { "yes" } else { "NO" });
    }

    // ---- 3. history in Parquet
    let points = hist_days * 86400 / hist_step_s as usize;
    let hist_sites = (hist_devices / 10).max(1);
    println!("\n3. history: {} devices x {} days at {} s = {} rows, written oldest first", thousands(hist_devices as f64), hist_days, hist_step_s, thousands((hist_devices * points) as f64));
    let end = now_ns() / NS * NS - hist_step_s * NS;
    let lines = sensor_lines("history", hist_devices, hist_sites, points, hist_step_s * NS, end, batch);
    let r = ingest(db.clone(), lines, hist_devices * points, writers, false).await?;
    println!("   wrote {} rows ({:.0} MB of line protocol) in {:.2} s = {} rows/s", thousands(r.rows as f64), r.bytes as f64 / 1e6, r.secs, thousands(r.rows as f64 / r.secs));
    // snapshots move the buffer to Parquet; poll system.parquet_files until every row is persisted
    let t = Instant::now();
    let pq_sql = "SELECT count(*) AS files, coalesce(sum(row_count), 0) AS rows, coalesce(sum(size_bytes), 0) AS bytes FROM system.parquet_files WHERE table_name = 'history'";
    let (mut files, mut prow, mut pbytes) = (0u64, 0u64, 0u64);
    while t.elapsed() < Duration::from_secs(env_or("PERSIST_WAIT_S", 180)) {
        let v = db.sql(pq_sql).await?;
        (files, prow, pbytes) = (v[0]["files"].as_u64().unwrap_or(0), v[0]["rows"].as_u64().unwrap_or(0), v[0]["bytes"].as_u64().unwrap_or(0));
        if prow as usize >= r.rows {
            break;
        }
        // keep the WAL moving like a live fleet would: one heartbeat row a second
        db.write(format!("heartbeat,source=showcase alive=1i {}\n", now_ns()), false).await?;
        tokio::time::sleep(Duration::from_secs(1)).await;
    }
    println!(
        "   persisted after {:.0} s: {} of {} rows in {} Parquet files, {:.1} MB ({:.1}x smaller than the line protocol, {:.1} bytes/row)",
        t.elapsed().as_secs_f64(), thousands(prow as f64), thousands(r.rows as f64), files, pbytes as f64 / 1e6,
        r.bytes as f64 / pbytes.max(1) as f64, pbytes as f64 / prow.max(1) as f64
    );
    let hdev = format!("dev-{:07}", hist_devices / 2);
    let hq = [
        ("1 device, last 6 h", format!("SELECT time, temp FROM history WHERE device_id = '{hdev}' AND time > now() - INTERVAL '6 hours' ORDER BY time")),
        ("1 device, 6 h window 6 days ago", format!("SELECT time, temp FROM history WHERE device_id = '{hdev}' AND time BETWEEN now() - INTERVAL '6 days' AND now() - INTERVAL '6 days' + INTERVAL '6 hours' ORDER BY time")),
        ("fleet hourly avg, last 24 h", "SELECT date_bin(INTERVAL '1 hour', time) AS h, avg(temp) AS t FROM history WHERE time > now() - INTERVAL '24 hours' GROUP BY 1 ORDER BY 1".to_string()),
        ("fleet daily avg, last 2 days", "SELECT date_bin(INTERVAL '1 day', time) AS d, avg(temp) AS t, min(battery) AS b FROM history WHERE time > now() - INTERVAL '2 days' GROUP BY 1 ORDER BY 1".to_string()),
        ("fleet daily avg, all days", "SELECT date_bin(INTERVAL '1 day', time) AS d, avg(temp) AS t, min(battery) AS b FROM history GROUP BY 1 ORDER BY 1".to_string()),
    ];
    println!("   {:<33} {:>6} {:>10} {:>9} {:>9}", "query", "rows", "first run", "p50", "p99");
    for (label, q) in &hq {
        match timed(&db, q, iter).await {
            Ok((rows, first, p50, p99)) => println!("   {label:<33} {rows:>6} {first:>8.1}ms {p50:>7.1}ms {p99:>7.1}ms"),
            Err(e) => println!("   {label:<33} error: {}", e.to_string().chars().take(600).collect::<String>()),
        }
    }
    // system.parquet_files must be filtered by table name (unfiltered it repeats files across tables)
    let mut parts = Vec::new();
    let tables = levels.iter().flat_map(|&s| [format!("ladder_{}", short(s)), format!("ladder_{}_ns", short(s))]).chain(["fleet".into(), "history".into()]);
    for t in tables {
        let v = db.sql(&format!("SELECT count(*) AS files, coalesce(sum(size_bytes), 0) AS bytes FROM system.parquet_files WHERE table_name = '{t}'")).await?;
        parts.push(format!("{t} {} / {:.1} MB", v[0]["files"], v[0]["bytes"].as_f64().unwrap_or(0.0) / 1e6));
    }
    println!("\n   Parquet in the object store (files / size): {}", parts.join(", "));
    println!("\ndone");
    Ok(())
}
