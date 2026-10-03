//! Market-data example: live tick ingestion over ILP while time-series SQL runs on the
//! freshest data.
//!
//! * Feed threads, each with its own ILP-over-HTTP `Sender` (official `questdb-rs` client),
//!   stream quotes and trades for their slice of the symbols, timestamped with the wall clock,
//!   for DURATION seconds at RATE rows/s in total (0: as fast as they can). FEEDS lists the
//!   feed counts to run one after the other (default `1,4`): one feed writes in time order,
//!   several feeds interleave their timestamps, so the WAL apply has to merge out-of-order rows.
//! * At the same time QUERY_CLIENTS PostgreSQL-wire connections (tokio-postgres) loop over
//!   prepared time-series queries on the last seconds/minute of data: data freshness, 1 s OHLCV
//!   bars, VWAP, 1 s mid-price bars, LATEST ON top of book, and an ASOF JOIN of trades to the
//!   quote in force (execution quality in basis points).
//! * After each run: wait until every row is applied, check the counts, print ingest and query
//!   statistics. At the end: what the queries see, a summary per run, and drop the tables
//!   (KEEP=1 keeps them).

use std::{
    collections::BTreeMap,
    env,
    error::Error,
    str::FromStr,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicU64, Ordering::Relaxed},
    },
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use questdb::ingress::{Sender, TimestampNanos};
use tokio_postgres::{Client, NoTls, Row};

type Res<T> = Result<T, Box<dyn Error + Send + Sync>>;

fn env_or<T: FromStr>(key: &str, default: T) -> T {
    env::var(key).ok().filter(|v| !v.is_empty()).and_then(|v| v.parse().ok()).unwrap_or(default)
}

#[derive(Clone)]
struct Cfg {
    host: String,
    feeds: usize,
    duration: u64,
    symbols: usize,
    batch: usize,
    rate: u64,
    query_clients: usize,
    keep: bool,
}

/// xorshift64*: enough randomness for a price walk, no dependency.
struct Rng(u64);
impl Rng {
    fn next(&mut self) -> u64 {
        self.0 ^= self.0 >> 12;
        self.0 ^= self.0 << 25;
        self.0 ^= self.0 >> 27;
        self.0.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }
    fn f64(&mut self) -> f64 {
        (self.next() >> 11) as f64 / (1u64 << 53) as f64
    }
}

const MAJORS: [(&str, f64); 10] = [
    ("BTC-USD", 65000.0), ("ETH-USD", 2500.0), ("SOL-USD", 150.0), ("XRP-USD", 0.55),
    ("ADA-USD", 0.38), ("DOGE-USD", 0.12), ("AVAX-USD", 27.0), ("DOT-USD", 4.3),
    ("LINK-USD", 11.5), ("LTC-USD", 66.0),
];

fn symbol(i: usize) -> (String, f64) {
    match MAJORS.get(i) {
        Some((s, p)) => (s.to_string(), *p),
        None => (format!("TKN{i:03}-USD"), 1.0 + (i * 37 % 500) as f64),
    }
}

fn round_to_tick(x: f64, tick: f64) -> f64 {
    (x / tick).round() * tick
}

#[derive(Default)]
struct FeedStats {
    quotes: AtomicU64,
    trades: AtomicU64,
    flush_us: Mutex<Vec<f64>>,
}

/// One feed handler: a random-walk mid per symbol, a 1 bp spread, a quote per event and a
/// trade on every third event on average (buys lift the ask, sells hit the bid).
fn feed(id: usize, cfg: Cfg, stop: Arc<AtomicBool>, stats: Arc<FeedStats>) -> questdb::Result<()> {
    let mut rng = Rng(0x9E37_79B9_7F4A_7C15 ^ (id as u64 + 1).wrapping_mul(0xBF58_476D_1CE4_E5B9));
    let mut book: Vec<(String, f64, f64)> = (id..cfg.symbols)
        .step_by(cfg.feeds)
        .map(|i| {
            let (s, p) = symbol(i);
            let tick = if p >= 1000.0 { 0.01 } else if p >= 1.0 { 0.001 } else { 0.00001 };
            (s, p, tick)
        })
        .collect();
    if book.is_empty() {
        return Ok(());
    }
    let mut sender = Sender::from_conf(format!(
        "http::addr={}:9000;request_timeout=60000;retry_timeout=10000;",
        cfg.host
    ))?;
    let mut buf = sender.new_buffer();
    let per_feed_rate = cfg.rate as f64 / cfg.feeds as f64;
    let started = Instant::now();
    let (mut sent, mut trade_id) = (0u64, (id as i64) << 40);
    while !stop.load(Relaxed) {
        let (mut q, mut t) = (0u64, 0u64);
        for _ in 0..cfg.batch {
            let k = (rng.next() % book.len() as u64) as usize;
            let (sym, mid, tick) = &mut book[k];
            *mid *= 1.0 + (rng.f64() - 0.5) * 0.00001;
            let half = (*mid * 0.00005).max(*tick / 2.0);
            let bid = round_to_tick(*mid - half, *tick);
            let ask = round_to_tick(*mid + half, *tick).max(bid + *tick);
            let ts = TimestampNanos::now();
            buf.table("md_quotes")?
                .symbol("symbol", sym.as_str())?
                .column_f64("bid", bid)?
                .column_f64("ask", ask)?
                .column_f64("bid_size", (1 + rng.next() % 5000) as f64)?
                .column_f64("ask_size", (1 + rng.next() % 5000) as f64)?
                .at(ts)?;
            q += 1;
            if rng.next() % 3 == 0 {
                let buy = rng.next() % 2 == 0;
                trade_id += 1;
                buf.table("md_trades")?
                    .symbol("symbol", sym.as_str())?
                    .symbol("side", if buy { "buy" } else { "sell" })?
                    .column_f64("price", if buy { ask } else { bid })?
                    .column_f64("qty", (1 + rng.next() % 2000) as f64 / 100.0)?
                    .column_i64("trade_id", trade_id)?
                    .at(ts)?;
                t += 1;
            }
        }
        let t0 = Instant::now();
        sender.flush(&mut buf)?; // returns once QuestDB acked: the rows are in the WAL
        stats.flush_us.lock().unwrap().push(t0.elapsed().as_secs_f64() * 1e6);
        stats.quotes.fetch_add(q, Relaxed);
        stats.trades.fetch_add(t, Relaxed);
        sent += q + t;
        if per_feed_rate > 0.0 {
            let due = Duration::from_secs_f64(sent as f64 / per_feed_rate);
            if let Some(wait) = due.checked_sub(started.elapsed()) {
                thread::sleep(wait);
            }
        }
    }
    Ok(())
}

/// The queries the dashboard side runs while the feeds write. `$1` is a symbol.
const QUERIES: [(&str, &str); 6] = [
    ("freshness (last trade)", "SELECT ts FROM md_trades LIMIT -1"),
    ("ohlcv 1s, 1 symbol, 1m",
     "SELECT ts, first(price) open, max(price) high, min(price) low, last(price) close, \
      sum(qty) volume, count() trades FROM md_trades \
      WHERE symbol = $1 AND ts > dateadd('m', -1, now()) SAMPLE BY 1s"),
    ("vwap all symbols, 1m",
     "SELECT symbol, sum(price * qty) / sum(qty) vwap, sum(qty) volume, count() trades \
      FROM md_trades WHERE ts > dateadd('m', -1, now()) ORDER BY trades DESC"),
    ("mid 1s bars all, 10s",
     "SELECT ts, symbol, avg((bid + ask) / 2) mid, count() quotes FROM md_quotes \
      WHERE ts > dateadd('s', -10, now()) SAMPLE BY 1s"),
    ("latest on (top of book)",
     "SELECT symbol, bid, ask, ts FROM md_quotes LATEST ON ts PARTITION BY symbol"),
    ("asof join trades/quotes, 1s",
     "SELECT t.symbol, t.side, count() trades, \
      avg((t.price - (q.bid + q.ask) / 2) / ((q.bid + q.ask) / 2)) * 10000 bps \
      FROM md_trades t ASOF JOIN md_quotes q ON (symbol) \
      WHERE t.ts > dateadd('s', -1, now()) ORDER BY t.symbol, t.side"),
];

#[derive(Default)]
struct QueryStats {
    lat_ms: BTreeMap<usize, Vec<f64>>,
    rows: BTreeMap<usize, u64>,
    lag_ms: Vec<f64>,
    errors: u64,
}

async fn connect(host: &str) -> Res<Client> {
    let (client, conn) = tokio_postgres::connect(
        &format!("host={host} port=8812 user=admin password=quest dbname=qdb"),
        NoTls,
    )
    .await?;
    tokio::spawn(async move {
        if let Err(e) = conn.await {
            eprintln!("pg connection: {e}");
        }
    });
    Ok(client)
}

fn now_micros() -> i64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_micros() as i64
}

fn micros(t: SystemTime) -> i64 {
    t.duration_since(UNIX_EPOCH).map(|d| d.as_micros() as i64).unwrap_or(0)
}

async fn query_loop(id: usize, cfg: Cfg, stop: Arc<AtomicBool>, stats: Arc<Mutex<QueryStats>>) -> Res<()> {
    let client = connect(&cfg.host).await?;
    let mut stmts = Vec::new();
    for (_, sql) in QUERIES {
        stmts.push(client.prepare(sql).await?);
    }
    let syms: Vec<String> = (0..cfg.symbols.min(10)).map(|i| symbol(i).0).collect();
    let mut n = id;
    while !stop.load(Relaxed) {
        let k = n % QUERIES.len();
        let sym = &syms[(n / QUERIES.len()) % syms.len()];
        n += 1;
        let t0 = Instant::now();
        let res = if QUERIES[k].1.contains("$1") {
            client.query(&stmts[k], &[sym]).await
        } else {
            client.query(&stmts[k], &[]).await
        };
        let ms = t0.elapsed().as_secs_f64() * 1e3;
        let mut s = stats.lock().unwrap();
        match res {
            Ok(rows) => {
                s.lat_ms.entry(k).or_default().push(ms);
                *s.rows.entry(k).or_default() += rows.len() as u64;
                if k == 0 {
                    if let Some(r) = rows.first() {
                        let last: SystemTime = r.get(0);
                        s.lag_ms.push((now_micros() - micros(last)) as f64 / 1e3);
                    }
                }
            }
            Err(e) => {
                s.errors += 1;
                if s.errors <= 3 {
                    let msg = e.as_db_error().map(|d| d.message().to_string()).unwrap_or(e.to_string());
                    eprintln!("query {}: {msg}", QUERIES[k].0);
                }
            }
        }
    }
    Ok(())
}

fn pct(v: &[f64], p: f64) -> f64 {
    if v.is_empty() {
        return f64::NAN;
    }
    let mut s = v.to_vec();
    s.sort_by(|a, b| a.partial_cmp(b).unwrap());
    s[((s.len() - 1) as f64 * p).round() as usize]
}

fn fmt_int(n: u64) -> String {
    let s = n.to_string();
    let mut out = String::new();
    for (i, c) in s.chars().enumerate() {
        if i > 0 && (s.len() - i) % 3 == 0 {
            out.push(',');
        }
        out.push(c);
    }
    out
}

async fn count(c: &Client, table: &str) -> Res<i64> {
    Ok(c.query_one(&format!("SELECT count() FROM {table}"), &[]).await?.get(0))
}

async fn setup(c: &Client) -> Res<()> {
    for sql in [
        "DROP TABLE IF EXISTS md_trades",
        "DROP TABLE IF EXISTS md_quotes",
        "CREATE TABLE md_trades (symbol SYMBOL CAPACITY 1024, side SYMBOL, price DOUBLE, \
         qty DOUBLE, trade_id LONG, ts TIMESTAMP) TIMESTAMP(ts) PARTITION BY HOUR WAL",
        "CREATE TABLE md_quotes (symbol SYMBOL CAPACITY 1024, bid DOUBLE, ask DOUBLE, \
         bid_size DOUBLE, ask_size DOUBLE, ts TIMESTAMP) TIMESTAMP(ts) PARTITION BY HOUR WAL",
    ] {
        c.simple_query(sql).await?;
    }
    Ok(())
}

fn print_rows(title: &str, rows: &[Row], cols: &[&str]) {
    println!("    {title}");
    println!("      {}", cols.iter().map(|c| format!("{c:>14}")).collect::<String>());
    for r in rows {
        let mut line = String::from("      ");
        for (i, _) in cols.iter().enumerate() {
            let cell = if let Ok(v) = r.try_get::<_, f64>(i) {
                if v.abs() >= 1000.0 { format!("{v:.2}") } else { format!("{v:.5}") }
            } else if let Ok(v) = r.try_get::<_, i64>(i) {
                v.to_string()
            } else if let Ok(v) = r.try_get::<_, String>(i) {
                v
            } else if let Ok(v) = r.try_get::<_, SystemTime>(i) {
                let us = micros(v);
                format!("{:02}:{:02}:{:02}.{:03}", us / 3_600_000_000 % 24, us / 60_000_000 % 60,
                        us / 1_000_000 % 60, us / 1000 % 1000)
            } else {
                "?".into()
            };
            line.push_str(&format!("{cell:>14}"));
        }
        println!("{line}");
    }
}

/// One live run: fresh tables, `feeds` ILP feeds and the query clients at the same time for
/// DURATION seconds, then catch-up and a count check. Returns a summary line for the table.
async fn phase(n: usize, admin: &Client, cfg: &Cfg, feeds_n: usize) -> Res<(String, bool)> {
    let mut cfg = cfg.clone();
    cfg.feeds = feeds_n;
    setup(admin).await?;
    let mode = if feeds_n == 1 { "in time order".to_string() } else { "interleaved, out of order".to_string() };
    println!(
        "{n}. live, {} feed{} ({mode}): {} events per ILP request, {} + {} PG-wire query clients, {} s",
        feeds_n,
        if feeds_n == 1 { "" } else { "s" },
        fmt_int(cfg.batch as u64),
        if cfg.rate > 0 { format!("{} rows/s in total", fmt_int(cfg.rate)) } else { "unthrottled".into() },
        cfg.query_clients,
        cfg.duration
    );

    let stop_feeds = Arc::new(AtomicBool::new(false));
    let stop_queries = Arc::new(AtomicBool::new(false));
    let fstats = Arc::new(FeedStats::default());
    let qstats = Arc::new(Mutex::new(QueryStats::default()));
    let feeds: Vec<_> = (0..cfg.feeds)
        .map(|i| {
            let (cfg, stop, stats) = (cfg.clone(), stop_feeds.clone(), fstats.clone());
            thread::spawn(move || feed(i, cfg, stop, stats))
        })
        .collect();
    // let the tables receive their first rows before the queries start
    while fstats.trades.load(Relaxed) == 0 && !feeds.iter().all(|f| f.is_finished()) {
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    let queriers: Vec<_> = (0..cfg.query_clients)
        .map(|i| tokio::spawn(query_loop(i, cfg.clone(), stop_queries.clone(), qstats.clone())))
        .collect();

    let started = Instant::now();
    let (mut last_rows, mut last_q) = (0u64, 0usize);
    for sec in 1..=cfg.duration {
        tokio::time::sleep_until((started + Duration::from_secs(sec)).into()).await;
        if feeds.iter().any(|f| f.is_finished()) {
            break; // a feed failed; its error is reported below
        }
        let rows = fstats.quotes.load(Relaxed) + fstats.trades.load(Relaxed);
        let s = qstats.lock().unwrap();
        let nq: usize = s.lat_ms.values().map(Vec::len).sum();
        let lag = s.lag_ms.last().copied().unwrap_or(f64::NAN);
        if sec % 5 == 0 || sec == 1 {
            println!(
                "   {sec:>4} s  {:>11} rows/s  {:>12} rows  {:>5} queries/s  last trade visible after {:.0} ms",
                fmt_int(rows - last_rows),
                fmt_int(rows),
                nq - last_q,
                lag
            );
        }
        last_rows = rows;
        last_q = nq;
    }
    stop_feeds.store(true, Relaxed);
    let live_s = started.elapsed().as_secs_f64();
    for f in feeds {
        f.join().expect("feed thread panicked")?;
    }
    stop_queries.store(true, Relaxed);
    for q in queriers {
        q.await??;
    }
    let acked_at = Instant::now();
    let (quotes, trades) = (fstats.quotes.load(Relaxed), fstats.trades.load(Relaxed));
    let total = quotes + trades;

    // catch up: every acked row must become visible
    let deadline = Instant::now() + Duration::from_secs(300);
    let (mut cq, mut ct);
    loop {
        cq = count(admin, "md_quotes").await? as u64;
        ct = count(admin, "md_trades").await? as u64;
        if (cq, ct) == (quotes, trades) || Instant::now() > deadline {
            break;
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    let catch_up_ms = acked_at.elapsed().as_secs_f64() * 1e3;
    let rate = (total as f64 / live_s) as u64;
    println!(
        "      ingested  {} rows ({} quotes + {} trades) in {:.1} s = {} rows/s acked",
        fmt_int(total), fmt_int(quotes), fmt_int(trades), live_s, fmt_int(rate)
    );
    let flush = fstats.flush_us.lock().unwrap().clone();
    println!(
        "                {} ILP requests, flush p50 {:.1} ms / p99 {:.1} ms; all rows visible {:.0} ms after the last ack",
        flush.len(), pct(&flush, 0.5) / 1e3, pct(&flush, 0.99) / 1e3, catch_up_ms
    );
    let ok = (cq, ct) == (quotes, trades);
    println!(
        "      check     count(): md_quotes {} / {} sent, md_trades {} / {} sent -> {}",
        fmt_int(cq), fmt_int(quotes), fmt_int(ct), fmt_int(trades), if ok { "ok" } else { "MISMATCH" }
    );

    let s = qstats.lock().unwrap();
    let nq: usize = s.lat_ms.values().map(Vec::len).sum();
    println!(
        "      queries   {} during ingest ({:.0}/s, {} errors); last trade visible p50 {:.0} ms / p99 {:.0} ms after its timestamp",
        fmt_int(nq as u64), nq as f64 / live_s, s.errors, pct(&s.lag_ms, 0.5), pct(&s.lag_ms, 0.99)
    );
    println!("        {:<30} {:>6} {:>9} {:>8} {:>8} {:>8}", "query", "runs", "rows/run", "p50 ms", "p99 ms", "max ms");
    let mut asof = (f64::NAN, f64::NAN);
    for (k, v) in &s.lat_ms {
        println!(
            "        {:<30} {:>6} {:>9.0} {:>8.2} {:>8.2} {:>8.2}",
            QUERIES[*k].0, v.len(), s.rows[k] as f64 / v.len() as f64,
            pct(v, 0.5), pct(v, 0.99), pct(v, 1.0)
        );
        if *k == QUERIES.len() - 1 {
            asof = (pct(v, 0.5), pct(v, 0.99));
        }
    }
    let line = format!(
        "  {:>5}  {:>12}  {:>10.0}  {:>10.0}  {:>10.0}  {:>12.1}  {:>12.1}",
        feeds_n, fmt_int(rate), nq as f64 / live_s, pct(&s.lag_ms, 0.5), pct(&s.lag_ms, 0.99), asof.0, asof.1
    );
    Ok((line, ok))
}

#[tokio::main(flavor = "multi_thread", worker_threads = 4)]
async fn main() -> Res<()> {
    let cfg = Cfg {
        host: env_or("QDB_HOST", "questdb".to_string()),
        feeds: 1,
        duration: env_or("DURATION", 20u64),
        symbols: env_or("SYMBOLS", 50usize).max(1),
        batch: env_or("BATCH", 10_000usize).max(1),
        rate: env_or("RATE", 500_000u64),
        query_clients: env_or("QUERY_CLIENTS", 4usize),
        keep: env_or("KEEP", 0u8) == 1,
    };
    // feed counts to run one after the other, e.g. FEEDS=1,4
    let feed_counts: Vec<usize> = env_or("FEEDS", "1,4".to_string())
        .split(',')
        .filter_map(|s| s.trim().parse().ok())
        .filter(|&n| n > 0)
        .collect();
    let admin = connect(&cfg.host).await?;
    let version: String = admin.query_one("SELECT build()", &[]).await?.get(0);
    println!("{version}");
    println!(
        "1. tables       md_trades, md_quotes: designated timestamp, PARTITION BY HOUR, WAL; {} symbols, \
         a quote per event and a trade on every third, timestamped with the wall clock",
        cfg.symbols
    );
    let mut lines = Vec::new();
    let mut all_ok = true;
    for (i, &f) in feed_counts.iter().enumerate() {
        let (line, ok) = phase(i + 2, &admin, &cfg, f).await?;
        lines.push(line);
        all_ok &= ok;
    }
    let n = feed_counts.len() + 2;

    // what the queries see, on the final data of the last run
    println!("{n}. results      the same SQL on the last run's data (time windows end at the last tick)");
    let last: SystemTime = admin.query_one("SELECT max(ts) FROM md_trades", &[]).await?.get(0);
    let at = format!("{}::timestamp", micros(last));
    let q = |sql: &str| sql.replace("now()", &at);
    let rows = admin
        .query(&format!("{} LIMIT -5", q(QUERIES[1].1)), &[&"BTC-USD"])
        .await?;
    print_rows("BTC-USD 1 s OHLCV, last 5 bars:", &rows, &["ts", "open", "high", "low", "close", "volume", "trades"]);
    let rows = admin.query(&format!("{} LIMIT 5", q(QUERIES[2].1)), &[]).await?;
    print_rows("VWAP over the last minute, 5 busiest symbols:", &rows, &["symbol", "vwap", "volume", "trades"]);
    let rows = admin.query(&format!("{} LIMIT 6", QUERIES[4].1), &[]).await?;
    print_rows("top of book (LATEST ON), first 6 symbols:", &rows, &["symbol", "bid", "ask", "ts"]);
    let rows = admin
        .query(&format!("SELECT side, sum(trades) trades, avg(bps) avg_bps FROM ({}) ORDER BY side", q(QUERIES[5].1)), &[])
        .await?;
    print_rows(
        "ASOF JOIN, last 1 s: trade price vs. quote mid in bp (buys lift the ask: ~+0.5, sells hit the bid: ~-0.5):",
        &rows, &["side", "trades", "avg_bps"],
    );

    println!("{}. summary", n + 1);
    println!(
        "  {:>5}  {:>12}  {:>10}  {:>10}  {:>10}  {:>12}  {:>12}",
        "feeds", "rows/s", "queries/s", "fresh p50", "fresh p99", "asof p50 ms", "asof p99 ms"
    );
    for l in &lines {
        println!("{l}");
    }
    if !cfg.keep {
        admin.simple_query("DROP TABLE md_trades").await?;
        admin.simple_query("DROP TABLE md_quotes").await?;
        println!("  dropped md_trades and md_quotes (KEEP=1 keeps the last run's tables)");
    }
    if !all_ok {
        return Err("row counts do not match what was sent".into());
    }
    println!("\nALL CHECKS PASSED");
    Ok(())
}
