//! TimescaleDB fleet telemetry example.
//!
//! 1. Relational metadata (fleets, vehicles) in plain Postgres tables.
//! 2. Telemetry from VEHICLES vehicles, one reading every INTERVAL_S seconds for DAYS days,
//!    generated here and streamed with binary COPY over WORKERS connections, twice: straight into
//!    the columnstore (direct compress) and into the rowstore.
//! 3. Dashboard queries (joins with the metadata) on the rowstore, on the columnstore after
//!    convert_to_columnstore, and on an hourly continuous aggregate.

use std::env;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use anyhow::{Context, Result, bail};
use futures_util::pin_mut;
use rand::rngs::SmallRng;
use rand::{Rng, SeedableRng};
use tokio_postgres::binary_copy::BinaryCopyInWriter;
use tokio_postgres::types::{ToSql, Type};
use tokio_postgres::{Client, NoTls};

const REGIONS: [(&str, f64, f64); 5] = [
    ("berlin", 52.52, 13.40),
    ("paris", 48.86, 2.35),
    ("madrid", 40.42, -3.70),
    ("milan", 45.46, 9.19),
    ("warsaw", 52.23, 21.01),
];
const MODELS: [&str; 4] = ["e-van 40", "e-van 75", "e-truck 200", "e-car 60"];

struct Config {
    conn: String,
    vehicles: i32,
    fleets: i32,
    days: i64,
    interval_s: i64,
    workers: usize,
    batch: usize,
    runs: usize,
}

fn env_or<T: std::str::FromStr>(key: &str, default: T) -> T {
    env::var(key).ok().and_then(|v| v.parse().ok()).unwrap_or(default)
}

impl Config {
    fn from_env() -> Self {
        let conn = format!(
            "host={} port={} user={} password={} dbname={}",
            env_or("PGHOST", "timescaledb".to_string()),
            env_or("PGPORT", 5432),
            env_or("PGUSER", "postgres".to_string()),
            env_or("PGPASSWORD", "tsdb-demo".to_string()),
            env_or("PGDATABASE", "postgres".to_string()),
        );
        Config {
            conn,
            vehicles: env_or("VEHICLES", 1000),
            fleets: env_or("FLEETS", 20),
            days: env_or("DAYS", 5),
            interval_s: env_or("INTERVAL_S", 30),
            workers: env_or("WORKERS", 4),
            batch: env_or("BATCH", 100_000),
            runs: env_or("RUNS", 5),
        }
    }
    fn ticks(&self) -> i64 {
        self.days * 86_400 / self.interval_s
    }
    fn rows(&self) -> i64 {
        self.ticks() * self.vehicles as i64
    }
}

async fn connect(cfg: &Config) -> Result<Client> {
    let (client, conn) = tokio_postgres::connect(&cfg.conn, NoTls).await.context("connect")?;
    tokio::spawn(async move {
        if let Err(e) = conn.await {
            eprintln!("connection error: {e}");
        }
    });
    Ok(client)
}

async fn one_i64(c: &Client, sql: &str) -> Result<i64> {
    Ok(c.query_one(sql, &[]).await.with_context(|| sql.to_string())?.get(0))
}

fn mib(b: i64) -> f64 {
    b as f64 / 1048576.0
}

// ---------------------------------------------------------------------------------------------
// schema and metadata

async fn create_schema(c: &Client, cfg: &Config) -> Result<()> {
    c.batch_execute(
        "SET client_min_messages = warning;
         DROP MATERIALIZED VIEW IF EXISTS telemetry_hourly CASCADE;
         DROP TABLE IF EXISTS telemetry, vehicles, fleets CASCADE;
         CREATE TABLE fleets (id int PRIMARY KEY, name text NOT NULL, region text NOT NULL);
         CREATE TABLE vehicles (
           id int PRIMARY KEY, fleet_id int NOT NULL REFERENCES fleets,
           model text NOT NULL, battery_kwh real NOT NULL);",
    )
    .await?;
    for f in 0..cfg.fleets {
        let region = REGIONS[f as usize % REGIONS.len()].0;
        c.execute(
            "INSERT INTO fleets VALUES ($1, $2, $3)",
            &[&f, &format!("fleet-{f:02}-{region}"), &region],
        )
        .await?;
    }
    c.execute(
        "INSERT INTO vehicles
         SELECT v, v % $2, ($3::text[])[1 + v % 4], (ARRAY[40, 75, 200, 60])[1 + v % 4]
         FROM generate_series(0, $1 - 1) v",
        &[&cfg.vehicles, &cfg.fleets, &MODELS.iter().map(|s| s.to_string()).collect::<Vec<_>>()],
    )
    .await?;
    Ok(())
}

async fn create_telemetry(c: &Client) -> Result<()> {
    c.batch_execute(
        "SET client_min_messages = warning;
         DROP MATERIALIZED VIEW IF EXISTS telemetry_hourly CASCADE;
         DROP TABLE IF EXISTS telemetry;
         CREATE TABLE telemetry (
           time         timestamptz NOT NULL,
           vehicle_id   int NOT NULL,
           lat          double precision,
           lon          double precision,
           speed_kmh    real,
           battery_pct  real,
           motor_temp_c real,
           odometer_km  double precision
         ) WITH (tsdb.hypertable, tsdb.partition_column = 'time', tsdb.chunk_interval = '1 day',
                 tsdb.segmentby = 'vehicle_id', tsdb.orderby = 'time DESC');
         CALL remove_columnstore_policy('telemetry', if_exists => true);
         CREATE INDEX ON telemetry (vehicle_id, time DESC);",
    )
    .await?;
    Ok(())
}

// ---------------------------------------------------------------------------------------------
// telemetry generator: one simulated vehicle, deterministic per vehicle id

struct Vehicle {
    id: i32,
    rng: SmallRng,
    lat: f64,
    lon: f64,
    heading: f64,
    speed: f64,
    battery: f64,
    charging: bool,
    temp: f64,
    overheat_left: i64,
    odometer: f64,
    shift_start: u32,
    shift_hours: u32,
}

struct Reading {
    lat: f64,
    lon: f64,
    speed: f32,
    battery: f32,
    temp: f32,
    odometer: f64,
}

fn round(x: f64, digits: i32) -> f64 {
    let m = 10f64.powi(digits);
    (x * m).round() / m
}

impl Vehicle {
    fn new(id: i32, fleets: i32) -> Self {
        let mut rng = SmallRng::seed_from_u64(id as u64);
        let (_, lat, lon) = REGIONS[(id % fleets) as usize % REGIONS.len()];
        Vehicle {
            id,
            lat: lat + rng.gen_range(-0.15..0.15),
            lon: lon + rng.gen_range(-0.2..0.2),
            heading: rng.gen_range(0.0..std::f64::consts::TAU),
            speed: 0.0,
            battery: rng.gen_range(60.0..100.0),
            charging: false,
            temp: 25.0,
            overheat_left: 0,
            odometer: rng.gen_range(5_000.0..80_000.0),
            shift_start: rng.gen_range(5..9),
            shift_hours: rng.gen_range(8..14),
            rng,
        }
    }

    fn step(&mut self, unix_s: i64, interval_s: i64) -> Reading {
        let hour = ((unix_s / 3600) % 24) as u32;
        let on_shift = hour >= self.shift_start && hour < self.shift_start + self.shift_hours;
        // drive (with stops) during the shift, park and charge outside it or when the battery is low
        if self.battery < 15.0 || (!on_shift && self.battery < 90.0) {
            self.charging = true;
        }
        if self.charging && (self.battery >= 95.0 || (on_shift && self.battery >= 60.0)) {
            self.charging = false;
        }
        let target = if on_shift && !self.charging && self.rng.gen_bool(0.85) {
            self.rng.gen_range(20.0..95.0)
        } else {
            0.0
        };
        self.speed = (0.7 * self.speed + 0.3 * target).max(0.0);
        if self.speed < 1.0 && target == 0.0 {
            self.speed = 0.0;
        }
        let km = self.speed * interval_s as f64 / 3600.0;
        self.odometer += km;
        self.heading += self.rng.gen_range(-0.3..0.3);
        self.lat += km / 111.0 * self.heading.cos();
        self.lon += km / (111.0 * self.lat.to_radians().cos()) * self.heading.sin();
        if self.charging {
            self.battery = (self.battery + 0.05 * interval_s as f64 / 30.0 * 4.0).min(100.0);
        } else {
            self.battery = (self.battery - km * 0.12 - 0.001).max(0.0);
        }
        // motor temperature follows speed; rare overheating episodes (~30 min) for the alert query
        if self.overheat_left == 0 && self.speed > 0.0 && self.rng.gen_bool(0.00005) {
            self.overheat_left = 1800 / interval_s;
        }
        let extra = if self.overheat_left > 0 {
            self.overheat_left -= 1;
            45.0
        } else {
            0.0
        };
        let target_temp = 25.0 + self.speed * 0.55 + extra;
        self.temp += 0.2 * (target_temp - self.temp) + self.rng.gen_range(-0.5..0.5);
        Reading {
            lat: round(self.lat, 6),
            lon: round(self.lon, 6),
            speed: round(self.speed, 1) as f32,
            battery: round(self.battery, 1) as f32,
            temp: round(self.temp, 1) as f32,
            odometer: round(self.odometer, 3),
        }
    }
}

// ---------------------------------------------------------------------------------------------
// ingest: WORKERS connections, each owns every WORKERS-th vehicle and streams its readings in
// time order (like a live fleet) as binary COPY statements of up to BATCH rows each

const COPY_TYPES: [Type; 8] = [
    Type::TIMESTAMPTZ,
    Type::INT4,
    Type::FLOAT8,
    Type::FLOAT8,
    Type::FLOAT4,
    Type::FLOAT4,
    Type::FLOAT4,
    Type::FLOAT8,
];

async fn ingest_worker(cfg: &Config, w: usize, start_s: i64, direct: bool) -> Result<u64> {
    let c = connect(cfg).await?;
    if direct {
        // TimescaleDB 2.2x: COPY compresses batches itself and writes columnstore chunks
        c.batch_execute("SET timescaledb.enable_direct_compress_copy = on").await?;
    }
    let mut fleet: Vec<Vehicle> = (0..cfg.vehicles)
        .filter(|v| *v as usize % cfg.workers == w)
        .map(|v| Vehicle::new(v, cfg.fleets))
        .collect();
    let ticks = cfg.ticks();
    let mut tick = 0i64;
    let mut written = 0u64;
    while tick < ticks {
        let sink = c
            .copy_in("COPY telemetry (time, vehicle_id, lat, lon, speed_kmh, battery_pct, motor_temp_c, odometer_km) FROM STDIN BINARY")
            .await?;
        let writer = BinaryCopyInWriter::new(sink, &COPY_TYPES);
        pin_mut!(writer);
        let mut in_batch = 0usize;
        while tick < ticks && in_batch + fleet.len() <= cfg.batch.max(fleet.len()) {
            let unix_s = start_s + tick * cfg.interval_s;
            let ts = UNIX_EPOCH + Duration::from_secs(unix_s as u64);
            for v in fleet.iter_mut() {
                let r = v.step(unix_s, cfg.interval_s);
                let row: [&(dyn ToSql + Sync); 8] =
                    [&ts, &v.id, &r.lat, &r.lon, &r.speed, &r.battery, &r.temp, &r.odometer];
                writer.as_mut().write(&row).await?;
            }
            in_batch += fleet.len();
            tick += 1;
        }
        written += writer.finish().await?;
    }
    Ok(written)
}

struct IngestResult {
    rows: u64,
    secs: f64,
    bytes: i64,
}

async fn ingest(cfg: &'static Config, c: &Client, start_s: i64, direct: bool) -> Result<IngestResult> {
    create_telemetry(c).await?;
    let t0 = Instant::now();
    let tasks: Vec<_> = (0..cfg.workers)
        .map(|w| tokio::spawn(async move { ingest_worker(cfg, w, start_s, direct).await }))
        .collect();
    let mut rows = 0;
    for t in tasks {
        rows += t.await??;
    }
    let secs = t0.elapsed().as_secs_f64();
    let bytes = one_i64(c, "SELECT hypertable_size('telemetry')").await?;
    Ok(IngestResult { rows, secs, bytes })
}

// ---------------------------------------------------------------------------------------------
// dashboard queries: $1 = the newest reading's time

struct Query {
    name: &'static str,
    what: &'static str,
    raw: &'static str,
    cagg: Option<&'static str>,
}

const QUERIES: [Query; 5] = [
    Query {
        name: "fleet-daily-km",
        what: "km driven per fleet per day, all days",
        raw: "SELECT f.name, d.day, round(sum(d.km)::numeric, 1)
              FROM (SELECT vehicle_id, time_bucket('1 day', time) AS day,
                           max(odometer_km) - min(odometer_km) AS km
                    FROM telemetry WHERE time <= $1::timestamptz GROUP BY 1, 2) d
              JOIN vehicles v ON v.id = d.vehicle_id JOIN fleets f ON f.id = v.fleet_id
              GROUP BY 1, 2 ORDER BY 1, 2",
        cagg: Some(
            "SELECT f.name, d.day, round(sum(d.km)::numeric, 1)
              FROM (SELECT vehicle_id, time_bucket('1 day', hour) AS day,
                           max(max_odometer) - min(min_odometer) AS km
                    FROM telemetry_hourly WHERE hour <= $1::timestamptz GROUP BY 1, 2) d
              JOIN vehicles v ON v.id = d.vehicle_id JOIN fleets f ON f.id = v.fleet_id
              GROUP BY 1, 2 ORDER BY 1, 2",
        ),
    },
    Query {
        name: "region-speed-24h",
        what: "hourly avg speed of moving vehicles per region, last 24 h",
        raw: "SELECT time_bucket('1 hour', t.time) AS hour, f.region,
                     avg(t.speed_kmh) FILTER (WHERE t.speed_kmh > 0)
              FROM telemetry t JOIN vehicles v ON v.id = t.vehicle_id JOIN fleets f ON f.id = v.fleet_id
              WHERE t.time > $1::timestamptz - interval '24 hours' AND t.time <= $1::timestamptz
              GROUP BY 1, 2 ORDER BY 1, 2",
        cagg: Some(
            "SELECT h.hour, f.region, sum(h.sum_moving_speed) / nullif(sum(h.n_moving), 0)
              FROM telemetry_hourly h JOIN vehicles v ON v.id = h.vehicle_id JOIN fleets f ON f.id = v.fleet_id
              WHERE h.hour > $1::timestamptz - interval '24 hours' AND h.hour <= $1::timestamptz
              GROUP BY 1, 2 ORDER BY 1, 2",
        ),
    },
    Query {
        name: "overheating-by-model",
        what: "vehicles over 90 C per model, all days",
        raw: "SELECT v.model, count(DISTINCT t.vehicle_id), max(t.motor_temp_c)
              FROM telemetry t JOIN vehicles v ON v.id = t.vehicle_id
              WHERE t.motor_temp_c > 90 AND t.time <= $1::timestamptz GROUP BY 1 ORDER BY 1",
        cagg: Some(
            "SELECT v.model, count(DISTINCT h.vehicle_id), max(h.max_motor_temp)
              FROM telemetry_hourly h JOIN vehicles v ON v.id = h.vehicle_id
              WHERE h.max_motor_temp > 90 AND h.hour <= $1::timestamptz GROUP BY 1 ORDER BY 1",
        ),
    },
    Query {
        name: "fleet-lastpoint",
        what: "latest position + battery of each vehicle in fleet 7",
        raw: "SELECT v.id, l.time, l.lat, l.lon, l.battery_pct
              FROM vehicles v CROSS JOIN LATERAL (
                SELECT time, lat, lon, battery_pct FROM telemetry t
                WHERE t.vehicle_id = v.id AND t.time > $1::timestamptz - interval '1 hour' AND t.time <= $1::timestamptz
                ORDER BY time DESC LIMIT 1) l
              WHERE v.fleet_id = 7",
        cagg: None,
    },
    Query {
        name: "vehicle-route-24h",
        what: "one vehicle's route (every reading), last 24 h",
        raw: "SELECT time, lat, lon, speed_kmh FROM telemetry
              WHERE vehicle_id = 42 AND time > $1::timestamptz - interval '24 hours' AND time <= $1::timestamptz ORDER BY time",
        cagg: None,
    },
];

async fn time_query(c: &Client, sql: &str, end: &SystemTime, runs: usize) -> Result<(f64, usize)> {
    let stmt = c.prepare(sql).await.with_context(|| sql.to_string())?;
    let mut ms = Vec::with_capacity(runs);
    let mut n = 0;
    for _ in 0..runs {
        let t0 = Instant::now();
        n = c.query(&stmt, &[end]).await?.len();
        ms.push(t0.elapsed().as_secs_f64() * 1000.0);
    }
    ms.sort_by(|a, b| a.partial_cmp(b).unwrap());
    Ok((ms[ms.len() / 2], n))
}

async fn run_queries(c: &Client, cfg: &Config, end: &SystemTime, cagg: bool) -> Result<Vec<(f64, usize)>> {
    let mut out = Vec::new();
    for q in QUERIES.iter() {
        let sql = if cagg { q.cagg.unwrap_or("") } else { q.raw };
        if sql.is_empty() {
            out.push((f64::NAN, 0));
            continue;
        }
        out.push(time_query(c, sql, end, cfg.runs).await?);
    }
    Ok(out)
}

const CONVERT: &str = "DO $$
DECLARE c regclass;
BEGIN
  FOR c IN SELECT show_chunks('telemetry') LOOP CALL convert_to_columnstore(c); END LOOP;
END $$";

const CAGG: &str = "CREATE MATERIALIZED VIEW telemetry_hourly
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket('1 hour', time) AS hour, vehicle_id,
       min(odometer_km) AS min_odometer, max(odometer_km) AS max_odometer,
       sum(speed_kmh) FILTER (WHERE speed_kmh > 0) AS sum_moving_speed,
       count(*) FILTER (WHERE speed_kmh > 0) AS n_moving,
       max(motor_temp_c) AS max_motor_temp, min(battery_pct) AS min_battery
FROM telemetry GROUP BY hour, vehicle_id WITH NO DATA";

// ---------------------------------------------------------------------------------------------

#[tokio::main]
async fn main() -> Result<()> {
    let cfg: &'static Config = Box::leak(Box::new(Config::from_env()));
    if cfg.workers == 0 || cfg.vehicles < cfg.workers as i32 {
        bail!("need VEHICLES >= WORKERS >= 1");
    }
    let c = connect(cfg).await?;
    let version: String = c
        .query_one("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'", &[])
        .await?
        .get(0);
    let pg: String = c.query_one("SHOW server_version", &[]).await?.get(0);

    // the data ends at the last full minute, so "last 24 h" means the last day of data
    let now_s = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs() as i64 / 60 * 60;
    let start_s = now_s - cfg.days * 86_400;
    let end = UNIX_EPOCH + Duration::from_secs((start_s + (cfg.ticks() - 1) * cfg.interval_s) as u64);

    println!(
        "TimescaleDB {version} on PostgreSQL {pg}\n{} vehicles in {} fleets, one reading every {} s for {} days = {} rows; \
         {} COPY connections, up to {} rows per COPY",
        cfg.vehicles, cfg.fleets, cfg.interval_s, cfg.days, cfg.rows(), cfg.workers, cfg.batch
    );
    create_schema(&c, cfg).await?;

    println!("\n== ingest (binary COPY)");
    let cs = ingest(cfg, &c, start_s, true).await?;
    println!(
        "  straight into the columnstore: {:>10} rows in {:>5.1} s = {:>9.0} rows/s, {:>7.1} MiB on disk",
        cs.rows, cs.secs, cs.rows as f64 / cs.secs, mib(cs.bytes)
    );
    let rs = ingest(cfg, &c, start_s, false).await?;
    println!(
        "  into the rowstore:             {:>10} rows in {:>5.1} s = {:>9.0} rows/s, {:>7.1} MiB on disk",
        rs.rows, rs.secs, rs.rows as f64 / rs.secs, mib(rs.bytes)
    );
    c.batch_execute("VACUUM ANALYZE telemetry").await?;
    let chunks = one_i64(&c, "SELECT count(*) FROM show_chunks('telemetry')").await?;

    println!("\n== queries on the rowstore ({chunks} chunks), median of {} runs", cfg.runs);
    let q_row = run_queries(&c, cfg, &end, false).await?;

    let t0 = Instant::now();
    c.batch_execute(CONVERT).await?;
    let convert_s = t0.elapsed().as_secs_f64();
    c.batch_execute("VACUUM ANALYZE telemetry").await?;
    let col_bytes = one_i64(&c, "SELECT hypertable_size('telemetry')").await?;
    println!(
        "== convert_to_columnstore: {:.1} s; {:.1} MiB -> {:.1} MiB = {:.1}x smaller",
        convert_s,
        mib(rs.bytes),
        mib(col_bytes),
        rs.bytes as f64 / col_bytes as f64
    );
    let q_col = run_queries(&c, cfg, &end, false).await?;

    let t0 = Instant::now();
    c.batch_execute(CAGG).await?;
    c.batch_execute("CALL refresh_continuous_aggregate('telemetry_hourly', NULL, now() - interval '1 hour')")
        .await?;
    let cagg_s = t0.elapsed().as_secs_f64();
    let cagg_rows = one_i64(&c, "SELECT count(*) FROM telemetry_hourly").await?;
    println!("== continuous aggregate telemetry_hourly: {cagg_rows} rows, built in {cagg_s:.1} s");
    let q_cagg = run_queries(&c, cfg, &end, true).await?;

    println!(
        "\n{:<22} {:>6} {:>11} {:>12} {:>10}   {}",
        "query (median ms)", "rows", "rowstore", "columnstore", "cont. agg", "what"
    );
    println!("{}", "-".repeat(110));
    for (i, q) in QUERIES.iter().enumerate() {
        let cagg = if q_cagg[i].0.is_nan() { "-".to_string() } else { format!("{:.1}", q_cagg[i].0) };
        println!(
            "{:<22} {:>6} {:>11.1} {:>12.1} {:>10}   {}",
            q.name, q_row[i].1, q_row[i].0, q_col[i].0, cagg, q.what
        );
    }

    // real-time aggregation: rows newer than the last refresh are in the aggregate already
    let ts = end + Duration::from_secs(cfg.interval_s as u64);
    c.execute(
        "INSERT INTO telemetry VALUES ($1, 0, 52.5, 13.4, 50, 80, 120, 1)",
        &[&ts],
    )
    .await?;
    let hot: i64 = c
        .query_one(
            "SELECT count(*) FROM telemetry_hourly WHERE vehicle_id = 0 AND max_motor_temp = 120",
            &[],
        )
        .await?
        .get(0);
    println!("\nreal-time aggregation: a reading inserted just now shows up in telemetry_hourly: {}", hot == 1);
    c.execute("DELETE FROM telemetry WHERE vehicle_id = 0 AND motor_temp_c = 120", &[]).await?;
    Ok(())
}
