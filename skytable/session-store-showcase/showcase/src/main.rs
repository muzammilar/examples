//! Skytable session-store showcase (see ../README.md): an API gateway's session store and
//! per-API-key rate limiter on one Skytable node, through the official async Rust driver.
//!
//! 1. schema        space `gateway`: sessions, quotas (sint64 tokens), counters
//! 2. load          sessions inserted one query per round trip, then pipelined (1 and N connections)
//! 3. scaling       point SELECTs on the primary key: connections x pipeline depth
//! 4. request path  auth + touch session + rate limit, 4 round trips vs one 4-query pipeline
//! 5. counters      `n += 1` in the server vs read-modify-write in the client, under contention
//! 6. audit         row count, sum of `hits` == requests served, token balances exact

use skytable::{
    query,
    response::{Response, Value},
    Config, ConnectionAsync, Pipeline, Query,
};
use std::{
    env, process,
    str::FromStr,
    sync::Arc,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use tokio::{sync::Barrier, task::JoinSet};

type Res<T> = Result<T, String>;

#[derive(Clone)]
struct Settings {
    host: String,
    port: u16,
    password: String,
    sessions: u64,
    load_single: u64,
    load_conns: usize,
    pipe: usize,
    scale_secs: f64,
    scale_conns: Vec<usize>,
    scale_depths: Vec<usize>,
    clients: usize,
    requests: u64,
    keys: u64,
    limit: i64,
    increments: u64,
    nodelay: bool,
}

fn var<T: FromStr>(k: &str, d: T) -> T {
    env::var(k).ok().and_then(|v| v.parse().ok()).unwrap_or(d)
}

fn list(k: &str, d: &str) -> Vec<usize> {
    env::var(k)
        .unwrap_or_else(|_| d.into())
        .split(',')
        .map(|v| v.trim().parse().unwrap_or_else(|_| die(format!("{k}: bad number {v:?}"))))
        .collect()
}

impl Settings {
    fn from_env() -> Self {
        let s = Self {
            host: var("SKYTABLE_HOST", "skytable".to_string()),
            port: var("SKYTABLE_PORT", 2003),
            password: env::var("SKYDB_PASSWORD").unwrap_or_else(|_| die("SKYDB_PASSWORD is not set")),
            sessions: var("SESSIONS", 200_000),
            load_single: var("LOAD_SINGLE", 20_000),
            load_conns: var("LOAD_CONNS", 8),
            pipe: var("PIPE", 500),
            scale_secs: var("SCALE_SECS", 2.0),
            scale_conns: list("SCALE_CONNS", "1,4,16,64"),
            scale_depths: list("SCALE_DEPTHS", "1,16"),
            clients: var("CLIENTS", 32),
            requests: var("REQUESTS", 2_000),
            keys: var("KEYS", 50),
            limit: var("LIMIT", 500),
            increments: var("INCREMENTS", 1_000),
            nodelay: var("NODELAY", 1u8) == 1,
        };
        if s.load_single > s.sessions || s.pipe == 0 || s.load_conns == 0 || s.clients == 0 || s.keys == 0 {
            die("need LOAD_SINGLE <= SESSIONS and nonzero PIPE, LOAD_CONNS, CLIENTS, KEYS");
        }
        s
    }
    fn config(&self) -> Config {
        Config::new(&self.host, self.port, "root", &self.password)
    }
}

fn die(msg: impl std::fmt::Display) -> ! {
    eprintln!("error: {msg}");
    process::exit(1)
}

async fn connect(s: &Settings) -> ConnectionAsync {
    connect_with(s, s.nodelay).await
}

async fn connect_with(s: &Settings, nodelay: bool) -> ConnectionAsync {
    // the server may still be starting when compose runs us
    for attempt in 0..30 {
        match s.config().connect_async().await {
            Ok(c) => {
                if nodelay {
                    set_nodelay_on_all_sockets();
                }
                return c;
            }
            Err(e) if attempt == 29 => die(format!("connect {}:{}: {e}", s.host, s.port)),
            Err(_) => tokio::time::sleep(Duration::from_millis(500)).await,
        }
    }
    unreachable!()
}

/// The driver (0.8.12) leaves Nagle's algorithm on and writes a pipeline as two writes (a short
/// header, then the queries). The server cannot answer before the second one arrives, and delays
/// its ACK of the first; Nagle holds the second until that ACK: ~40 ms per small pipeline. The
/// driver doesn't expose its socket, so set TCP_NODELAY on every socket this process has open
/// (they are all Skytable connections; ENOTSOCK for the rest is ignored).
fn set_nodelay_on_all_sockets() {
    let Ok(dir) = std::fs::read_dir("/proc/self/fd") else { return };
    for fd in dir.flatten().filter_map(|e| e.file_name().to_str()?.parse::<i32>().ok()) {
        let one: libc::c_int = 1;
        unsafe {
            libc::setsockopt(
                fd,
                libc::IPPROTO_TCP,
                libc::TCP_NODELAY,
                &one as *const _ as *const libc::c_void,
                std::mem::size_of::<libc::c_int>() as libc::socklen_t,
            );
        }
    }
}

// ---- helpers ------------------------------------------------------------------------------

/// splitmix64: session i's token is a stable pseudo-random 16-hex-digit string
fn mix(mut x: u64) -> u64 {
    x = x.wrapping_add(0x9e37_79b9_7f4a_7c15);
    x = (x ^ (x >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
    x = (x ^ (x >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
    x ^ (x >> 31)
}

fn token(i: u64) -> String {
    format!("{:016x}", mix(i))
}

fn api_key(k: u64) -> String {
    format!("key-{k:04}")
}

struct Rng(u64);
impl Rng {
    fn new(seed: u64) -> Self {
        Self(mix(seed) | 1)
    }
    fn below(&mut self, n: u64) -> u64 {
        self.0 ^= self.0 << 13;
        self.0 ^= self.0 >> 7;
        self.0 ^= self.0 << 17;
        self.0 % n
    }
}

fn now_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_millis() as u64
}

/// percentile of sorted nanosecond samples, in microseconds
fn pct_us(sorted: &[u64], p: f64) -> f64 {
    if sorted.is_empty() {
        return 0.0;
    }
    let i = ((p / 100.0) * (sorted.len() - 1) as f64).round() as usize;
    sorted[i] as f64 / 1e3
}

fn rate(n: u64, d: Duration) -> String {
    group((n as f64 / d.as_secs_f64()).round() as u64)
}

fn group(n: u64) -> String {
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

fn ok(r: Response, what: &str) -> Res<Response> {
    match r {
        Response::Error(code) => Err(format!("{what}: server error {code}")),
        r => Ok(r),
    }
}

/// the single value of a one-column SELECT
fn value(r: Response, what: &str) -> Res<Value> {
    match ok(r, what)? {
        Response::Row(row) => row.into_first().map_err(|e| format!("{what}: {e}")),
        Response::Value(v) => Ok(v),
        other => Err(format!("{what}: unexpected response {other:?}")),
    }
}

fn as_i64(v: Value, what: &str) -> Res<i64> {
    v.parse::<i64>().map_err(|e| format!("{what}: {e}"))
}

fn as_u64(v: Value, what: &str) -> Res<u64> {
    v.parse::<u64>().map_err(|e| format!("{what}: {e}"))
}

async fn run(c: &mut ConnectionAsync, q: Query, what: &str) -> Res<Response> {
    let r = c.query(&q).await.map_err(|e| format!("{what}: {e}"))?;
    ok(r, what)
}

async fn pipeline(c: &mut ConnectionAsync, p: &Pipeline, what: &str) -> Res<Vec<Response>> {
    c.execute_pipeline(p).await.map_err(|e| format!("{what}: {e}"))
}

fn insert_session(i: u64, now: u64) -> Query {
    let (t, user) = (token(i), i % 50_000);
    let ip = (i % 3 != 0).then(|| format!("10.{}.{}.{}", i >> 16 & 255, i >> 8 & 255, i & 255));
    if i % 10 == 0 {
        query!(
            "insert into gateway.sessions(?, ?, ?, ?, ?, [?, ?], ?)",
            t, user, now, now, 0u64, "user", "admin", ip
        )
    } else {
        query!("insert into gateway.sessions(?, ?, ?, ?, ?, [?], ?)", t, user, now, now, 0u64, "user", ip)
    }
}

fn expect_all(rs: Vec<Response>, pred: fn(&Response) -> bool, what: &str) -> Res<()> {
    for r in rs {
        if !pred(&r) {
            return Err(format!("{what}: unexpected response {r:?}"));
        }
    }
    Ok(())
}

// ---- 1. schema --------------------------------------------------------------------------

async fn schema(s: &Settings) -> Res<()> {
    let mut c = connect(s).await;
    c.query(&query!("drop space if exists allow not empty gateway"))
        .await
        .map_err(|e| e.to_string())?;
    for q in [
        "create space gateway",
        "create model gateway.sessions(token: string, user_id: uint64, created_at: uint64, \
         last_seen: uint64, hits: uint64, roles: [string], null ip: string)",
        "create model gateway.quotas(api_key: string, tokens: sint64)",
        "create model gateway.counters(name: string, n: uint64)",
    ] {
        run(&mut c, query!(q), q).await?;
    }
    println!("1. schema        space gateway: sessions(token -> user_id, timestamps, hits, roles: [string], null ip),");
    println!("                 quotas(api_key -> tokens: sint64), counters(name -> n: uint64)");
    Ok(())
}

// ---- 2. load ----------------------------------------------------------------------------

async fn load(s: &Settings) -> Res<()> {
    let now = now_ms();
    let mut c = connect(s).await;
    // a) one INSERT per round trip
    let t = Instant::now();
    for i in 0..s.load_single {
        run(&mut c, insert_session(i, now), "insert").await?;
    }
    let single = t.elapsed();
    // b) pipelines of PIPE INSERTs on the same connection, half of the rest
    let rest = s.sessions - s.load_single;
    let (b_from, b_to) = (s.load_single, s.load_single + rest / 2);
    let t = Instant::now();
    let mut i = b_from;
    while i < b_to {
        let end = (i + s.pipe as u64).min(b_to);
        let p: Pipeline = (i..end).map(|j| insert_session(j, now)).collect();
        expect_all(pipeline(&mut c, &p, "insert pipeline").await?, |r| *r == Response::Empty, "insert")?;
        i = end;
    }
    let piped = t.elapsed();
    // c) the other half: pipelines on LOAD_CONNS connections at once
    let (c_from, c_to) = (b_to, s.sessions);
    let per = (c_to - c_from).div_ceil(s.load_conns as u64);
    let t = Instant::now();
    let mut set = JoinSet::new();
    for w in 0..s.load_conns as u64 {
        let (s, from, to) = (s.clone(), c_from + w * per, (c_from + (w + 1) * per).min(c_to));
        set.spawn(async move {
            let mut c = connect(&s).await;
            let mut i = from;
            while i < to {
                let end = (i + s.pipe as u64).min(to);
                let p: Pipeline = (i..end).map(|j| insert_session(j, now)).collect();
                expect_all(pipeline(&mut c, &p, "insert pipeline").await?, |r| *r == Response::Empty, "insert")?;
                i = end;
            }
            Res::Ok(())
        });
    }
    while let Some(r) = set.join_next().await {
        r.map_err(|e| e.to_string())??;
    }
    let parallel = t.elapsed();
    let base = s.load_single as f64 / single.as_secs_f64();
    let speed = |n: u64, d: Duration| (n as f64 / d.as_secs_f64()) / base;
    println!(
        "2. load          {} sessions; one INSERT per round trip: {} in {:.2} s = {} rows/s",
        group(s.sessions), group(s.load_single), single.as_secs_f64(), rate(s.load_single, single)
    );
    println!(
        "                 pipelines of {}, 1 connection:  {} in {:.2} s = {} rows/s ({:.0}x)",
        s.pipe, group(b_to - b_from), piped.as_secs_f64(), rate(b_to - b_from, piped), speed(b_to - b_from, piped)
    );
    println!(
        "                 pipelines of {}, {} connections: {} in {:.2} s = {} rows/s ({:.0}x)",
        s.pipe, s.load_conns, group(c_to - c_from), parallel.as_secs_f64(), rate(c_to - c_from, parallel),
        speed(c_to - c_from, parallel)
    );
    Ok(())
}

// ---- 3. scaling -------------------------------------------------------------------------

struct Run {
    ops: u64,
    elapsed: Duration,
    lat: Vec<u64>, // ns per round trip
}

async fn select_run(s: &Settings, conns: usize, depth: usize, nodelay: bool) -> Res<Run> {
    let barrier = Arc::new(Barrier::new(conns + 1));
    let dur = Duration::from_secs_f64(s.scale_secs);
    let mut set = JoinSet::new();
    for w in 0..conns {
        let (s, barrier) = (s.clone(), barrier.clone());
        set.spawn(async move {
            let mut c = connect_with(&s, nodelay).await;
            let mut rng = Rng::new(w as u64 + 1);
            let (mut ops, mut lat) = (0u64, Vec::with_capacity(1 << 16));
            barrier.wait().await;
            let start = Instant::now();
            while start.elapsed() < dur {
                if depth == 1 {
                    let q = query!(
                        "select user_id, hits from gateway.sessions where token = ?",
                        token(rng.below(s.sessions))
                    );
                    let t = Instant::now();
                    let r = run(&mut c, q, "select").await?;
                    lat.push(t.elapsed().as_nanos() as u64);
                    if !matches!(r, Response::Row(_)) {
                        return Err(format!("select: {r:?}"));
                    }
                } else {
                    let p: Pipeline = (0..depth)
                        .map(|_| {
                            query!(
                                "select user_id, hits from gateway.sessions where token = ?",
                                token(rng.below(s.sessions))
                            )
                        })
                        .collect();
                    let t = Instant::now();
                    let rs = pipeline(&mut c, &p, "select pipeline").await?;
                    lat.push(t.elapsed().as_nanos() as u64);
                    expect_all(rs, |r| matches!(r, Response::Row(_)), "select")?;
                }
                ops += depth as u64;
            }
            Res::Ok((ops, lat, start.elapsed()))
        });
    }
    barrier.wait().await;
    let (mut ops, mut lat, mut elapsed) = (0, Vec::new(), Duration::ZERO);
    while let Some(r) = set.join_next().await {
        let (o, l, e) = r.map_err(|e| e.to_string())??;
        ops += o;
        lat.extend(l);
        elapsed = elapsed.max(e);
    }
    lat.sort_unstable();
    Ok(Run { ops, elapsed, lat })
}

async fn scaling(s: &Settings) -> Res<()> {
    println!(
        "3. scaling       point SELECT on the primary key, random sessions, {:.0} s per row; latency per round trip",
        s.scale_secs
    );
    println!("                 {:>11} {:>6} {:>12} {:>9} {:>9}", "connections", "depth", "queries/s", "p50 us", "p99 us");
    let row = |conns: usize, depth: usize, r: &Run, note: &str| {
        println!(
            "                 {:>11} {:>6} {:>12} {:>9.1} {:>9.1}{note}",
            conns, depth, rate(r.ops, r.elapsed), pct_us(&r.lat, 50.0), pct_us(&r.lat, 99.0)
        )
    };
    for &depth in &s.scale_depths {
        for &conns in &s.scale_conns {
            row(conns, depth, &select_run(s, conns, depth, s.nodelay).await?, "");
        }
    }
    if s.nodelay {
        if let Some(&depth) = s.scale_depths.iter().find(|&&d| d > 1) {
            let r = select_run(s, 1, depth, false).await?;
            row(1, depth, &r, "   <- driver default, Nagle on (NODELAY=0)");
        }
    }
    Ok(())
}

// ---- 4. request path --------------------------------------------------------------------

#[derive(Clone, Copy, PartialEq)]
enum Mode {
    RoundTrips,
    Pipelined,
}

struct Served {
    attempts: Vec<u64>, // per api key
    admitted: Vec<u64>,
    lat: Vec<u64>,
    elapsed: Duration,
}

/// One API request: authenticate the session, touch it, take a token from the key's quota and
/// read the balance back. `tokens -= 1` is applied atomically by the server; the read after it
/// may already include other clients' decrements, so a request is only refused too often, never
/// admitted beyond the limit.
fn request_queries(rng: &mut Rng, s: &Settings) -> (u64, [Query; 4]) {
    let (t, k) = (token(rng.below(s.sessions)), rng.below(s.keys));
    let key = api_key(k);
    (
        k,
        [
            query!("select user_id, roles from gateway.sessions where token = ?", &t),
            query!("update gateway.sessions set last_seen = ?, hits += ? where token = ?", now_ms(), 1u64, &t),
            query!("update gateway.quotas set tokens -= ? where api_key = ?", 1i64, &key),
            query!("select tokens from gateway.quotas where api_key = ?", &key),
        ],
    )
}

fn check_request(rs: Vec<Response>) -> Res<i64> {
    let mut it = rs.into_iter();
    let (auth, touch, take, read) = (it.next(), it.next(), it.next(), it.next());
    match auth.ok_or("missing response")? {
        Response::Row(row) if row.len() == 2 => {}
        other => return Err(format!("auth: {other:?}")),
    }
    ok(touch.ok_or("missing response")?, "touch")?;
    ok(take.ok_or("missing response")?, "take token")?;
    as_i64(value(read.ok_or("missing response")?, "read tokens")?, "tokens")
}

async fn serve(s: &Settings, mode: Mode) -> Res<Served> {
    // refill every key's quota
    let mut c = connect(s).await;
    let p: Pipeline = (0..s.keys)
        .map(|k| query!("update gateway.quotas set tokens = ? where api_key = ?", s.limit, api_key(k)))
        .collect();
    expect_all(pipeline(&mut c, &p, "refill").await?, |r| *r == Response::Empty, "refill")?;

    let barrier = Arc::new(Barrier::new(s.clients + 1));
    let mut set = JoinSet::new();
    for w in 0..s.clients {
        let (s, barrier) = (s.clone(), barrier.clone());
        set.spawn(async move {
            let mut c = connect(&s).await;
            let mut rng = Rng::new(1_000 + w as u64 + if mode == Mode::Pipelined { 7_919 } else { 0 });
            let (mut attempts, mut admitted) = (vec![0u64; s.keys as usize], vec![0u64; s.keys as usize]);
            let mut lat = Vec::with_capacity(s.requests as usize);
            barrier.wait().await;
            for _ in 0..s.requests {
                let (k, qs) = request_queries(&mut rng, &s);
                let t = Instant::now();
                let rs = match mode {
                    Mode::RoundTrips => {
                        let mut rs = Vec::with_capacity(4);
                        for q in qs {
                            rs.push(c.query(&q).await.map_err(|e| e.to_string())?);
                        }
                        rs
                    }
                    Mode::Pipelined => pipeline(&mut c, &qs.iter().collect(), "request").await?,
                };
                lat.push(t.elapsed().as_nanos() as u64);
                attempts[k as usize] += 1;
                if check_request(rs)? >= 0 {
                    admitted[k as usize] += 1;
                }
            }
            Res::Ok((attempts, admitted, lat))
        });
    }
    barrier.wait().await;
    let start = Instant::now();
    let mut out = Served {
        attempts: vec![0; s.keys as usize],
        admitted: vec![0; s.keys as usize],
        lat: Vec::new(),
        elapsed: Duration::ZERO,
    };
    while let Some(r) = set.join_next().await {
        let (a, d, l) = r.map_err(|e| e.to_string())??;
        for k in 0..s.keys as usize {
            out.attempts[k] += a[k];
            out.admitted[k] += d[k];
        }
        out.lat.extend(l);
    }
    out.elapsed = start.elapsed();
    out.lat.sort_unstable();
    Ok(out)
}

/// every key's balance must be exactly LIMIT - attempts (no lost decrement), and no key may
/// have admitted more than LIMIT requests
async fn audit_quotas(s: &Settings, sv: &Served) -> Res<(u64, u64)> {
    let mut c = connect(s).await;
    let p: Pipeline = (0..s.keys)
        .map(|k| query!("select tokens from gateway.quotas where api_key = ?", api_key(k)))
        .collect();
    let rs = pipeline(&mut c, &p, "audit quotas").await?;
    let (mut wrong, mut over) = (0, 0);
    for (k, r) in rs.into_iter().enumerate() {
        let tokens = as_i64(value(r, "audit tokens")?, "tokens")?;
        if tokens != s.limit - sv.attempts[k] as i64 {
            wrong += 1;
        }
        if sv.admitted[k] > s.limit as u64 {
            over += 1;
        }
    }
    Ok((wrong, over))
}

async fn request_path(s: &Settings, failures: &mut Vec<String>) -> Res<u64> {
    let mut c = connect(s).await;
    let p: Pipeline = (0..s.keys)
        .map(|k| query!("insert into gateway.quotas(?, ?)", api_key(k), s.limit))
        .collect();
    expect_all(pipeline(&mut c, &p, "quotas").await?, |r| *r == Response::Empty, "insert quotas")?;
    let total = s.clients as u64 * s.requests;
    println!(
        "4. request path  {} clients x {} requests: select session, update last_seen + hits += 1, \
         tokens -= 1 on one of {} API keys ({} tokens each), read tokens",
        s.clients, group(s.requests), s.keys, s.limit
    );
    let mut first: Option<f64> = None;
    for (mode, label) in [(Mode::RoundTrips, "4 round trips"), (Mode::Pipelined, "1 pipeline   ")] {
        let sv = serve(s, mode).await?;
        let admitted: u64 = sv.admitted.iter().sum();
        let fair: u64 = sv.attempts.iter().map(|&a| a.min(s.limit as u64)).sum();
        let (wrong, over) = audit_quotas(s, &sv).await?;
        let rps = total as f64 / sv.elapsed.as_secs_f64();
        let x = first.map(|f| format!(" ({:.1}x)", rps / f)).unwrap_or_default();
        first.get_or_insert(rps);
        println!(
            "                 {label}: {} requests/s{x}, p50 {:.0} us, p99 {:.0} us per request",
            group(rps.round() as u64), pct_us(&sv.lat, 50.0), pct_us(&sv.lat, 99.0)
        );
        println!(
            "                   admitted {}, refused {} ({} refused although a token was left: read after others' decrements);",
            group(admitted), group(total - admitted), group(fair - admitted)
        );
        println!(
            "                   balances off by a lost decrement: {wrong} of {} keys; keys over their limit: {over}",
            s.keys
        );
        if wrong > 0 || over > 0 {
            failures.push(format!("request path ({}): {wrong} wrong balances, {over} keys over limit", label.trim()));
        }
    }
    Ok(2 * total)
}

// ---- 5. counters ------------------------------------------------------------------------

async fn counters(s: &Settings, failures: &mut Vec<String>) -> Res<()> {
    let mut c = connect(s).await;
    for name in ["atomic", "read-modify-write"] {
        run(&mut c, query!("insert into gateway.counters(?, ?)", name, 0u64), "counter").await?;
    }
    let expected = s.clients as u64 * s.increments;
    let mut results = Vec::new();
    for rmw in [false, true] {
        let barrier = Arc::new(Barrier::new(s.clients + 1));
        let mut set = JoinSet::new();
        for _ in 0..s.clients {
            let (s, barrier) = (s.clone(), barrier.clone());
            set.spawn(async move {
                let mut c = connect(&s).await;
                barrier.wait().await;
                for _ in 0..s.increments {
                    if rmw {
                        let r = run(
                            &mut c,
                            query!("select n from gateway.counters where name = ?", "read-modify-write"),
                            "read",
                        )
                        .await?;
                        let n = as_u64(value(r, "read")?, "n")?;
                        run(
                            &mut c,
                            query!("update gateway.counters set n = ? where name = ?", n + 1, "read-modify-write"),
                            "write",
                        )
                        .await?;
                    } else {
                        run(
                            &mut c,
                            query!("update gateway.counters set n += ? where name = ?", 1u64, "atomic"),
                            "increment",
                        )
                        .await?;
                    }
                }
                Res::Ok(())
            });
        }
        barrier.wait().await;
        let t = Instant::now();
        while let Some(r) = set.join_next().await {
            r.map_err(|e| e.to_string())??;
        }
        results.push(t.elapsed());
    }
    let mut finals = Vec::new();
    for name in ["atomic", "read-modify-write"] {
        let r = run(&mut c, query!("select n from gateway.counters where name = ?", name), "read").await?;
        finals.push(as_u64(value(r, "read")?, "n")?);
    }
    println!(
        "5. counters      {} clients x {} increments of one row (expected {}):",
        s.clients, group(s.increments), group(expected)
    );
    println!(
        "                 `n += 1` in the server:      n = {} ({} lost) in {:.2} s",
        group(finals[0]), group(expected - finals[0].min(expected)), results[0].as_secs_f64()
    );
    println!(
        "                 select n, then `n = n + 1`: n = {} ({} lost updates) in {:.2} s",
        group(finals[1]), group(expected - finals[1].min(expected)), results[1].as_secs_f64()
    );
    if finals[0] != expected {
        failures.push(format!("atomic counter is {} instead of {expected}", finals[0]));
    }
    Ok(())
}

// ---- 6. audit ---------------------------------------------------------------------------

async fn audit(s: &Settings, served: u64, failures: &mut Vec<String>) -> Res<()> {
    let mut c = connect(s).await;
    let info = value(run(&mut c, query!("inspect model gateway.sessions"), "inspect").await?, "inspect")?;
    let info: String = info.parse().map_err(|e| format!("inspect: {e}"))?;
    let rows: u64 = info
        .split("\"rows\":")
        .nth(1)
        .and_then(|r| r.split(|c: char| !c.is_ascii_digit()).next())
        .and_then(|r| r.parse().ok())
        .ok_or_else(|| format!("inspect: no row count in {info}"))?;
    let t = Instant::now();
    let mut hits = 0u64;
    let mut i = 0;
    while i < s.sessions {
        let end = (i + 1_000).min(s.sessions);
        let p: Pipeline = (i..end)
            .map(|j| query!("select hits from gateway.sessions where token = ?", token(j)))
            .collect();
        for r in pipeline(&mut c, &p, "audit").await? {
            hits += as_u64(value(r, "hits")?, "hits")?;
        }
        i = end;
    }
    let d = t.elapsed();
    println!(
        "6. audit         INSPECT MODEL: {} rows; sum(hits) over all sessions = {} (requests served: {}), \
         read with pipelined point SELECTs in {:.2} s",
        group(rows), group(hits), group(served), d.as_secs_f64()
    );
    if rows != s.sessions {
        failures.push(format!("{rows} sessions stored, expected {}", s.sessions));
    }
    if hits != served {
        failures.push(format!("sum(hits) {hits} != requests served {served}"));
    }
    Ok(())
}

#[tokio::main]
async fn main() {
    let s = Settings::from_env();
    let mut failures = Vec::new();
    let result: Res<()> = async {
        schema(&s).await?;
        load(&s).await?;
        scaling(&s).await?;
        let served = request_path(&s, &mut failures).await?;
        counters(&s, &mut failures).await?;
        audit(&s, served, &mut failures).await
    }
    .await;
    if let Err(e) = result {
        die(e);
    }
    if failures.is_empty() {
        println!("\nALL CHECKS PASSED");
    } else {
        for f in &failures {
            eprintln!("FAILED: {f}");
        }
        process::exit(1);
    }
}
