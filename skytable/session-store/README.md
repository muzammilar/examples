# Skytable — session store (Rust client)

An API gateway's session store and per-API-key rate limiter on one Skytable node, driven by
[`app/`](app): a Rust program using the official async driver
([`skytable`](https://crates.io/crates/skytable) 0.8.12 from crates.io). Sessions are typed rows
looked up by primary key. Counters and token buckets are updated in place by the server
(`hits += 1`, `tokens -= 1`). Pipelines carry many queries per round trip.

## Quick start

```bash
make up      # start skyd (4 CPUs / 4 GB)
make run     # build the app image (first time: about a minute) and run it
make status  # container state, server version, INSPECT MODEL gateway.sessions
make cli     # interactive skysh as root
make down    # remove the containers, the data volume and the built image
```

Client port: `localhost:2013` (override with `SKYTABLE_PORT`).

What `make run` does (it drops and recreates space `gateway`, so it can be re-run):

1. **schema**: `sessions(token: string, user_id, created_at, last_seen, hits: uint64,
   roles: [string], null ip: string)`, `quotas(api_key: string, tokens: sint64)` and
   `counters(name: string, n: uint64)`. The token buckets use `sint64`, which skysh can't
   write (see [`../single-node`](../single-node/README.md#blueql)) but the driver can.
2. **load**: 200,000 sessions (some with two roles, a third with a `null` ip). The first 20,000
   go one `INSERT` per round trip. Of the rest, half go in pipelines of 500 on one connection,
   and half in pipelines on 8 connections at once.
3. **scaling**: point `SELECT user_id, hits ... WHERE token = ?` on random sessions for 2 s per
   setting, at 1/4/16/64 connections, either one query per round trip (depth 1) or pipelines of
   16. A last row repeats 1 connection × depth 16 with the driver's default socket settings (see
   below).
4. **request path**: 32 clients × 2,000 API requests. Each request selects the session (auth),
   sets `last_seen` and `hits += 1`, does `tokens -= 1` on one of 50 API keys (500 tokens each)
   and reads the balance back. A request is admitted if the balance is ≥ 0. Run once as 4 round
   trips and once as a single 4-query pipeline, refilling the buckets in between. The checks:
   every key's balance must equal exactly `500 - attempts` (no decrement lost), and no key may
   admit more than 500.
5. **counters**: 32 clients × 1,000 increments of one row, as `n += 1` in the server and as
   `select n` then `set n = n + 1` in the client.
6. **audit**: `INSPECT MODEL` must report 200,000 rows. `sum(hits)` over every session, read
   with pipelined point SELECTs, must equal the 128,000 requests served. Exits non-zero
   otherwise, and also when the atomic counter is off.

Override the sizes with `SESSIONS`, `LOAD_SINGLE`, `LOAD_CONNS`, `PIPE`, `SCALE_SECS`,
`SCALE_CONNS`, `SCALE_DEPTHS`, `CLIENTS`, `REQUESTS`, `KEYS`, `LIMIT`, `INCREMENTS`, e.g.
`make run CLIENTS=8 SCALE_CONNS=1,8 SCALE_DEPTHS=1,64`.

## The driver and TCP_NODELAY

The driver leaves Nagle's algorithm on, and `execute_pipeline` sends a pipeline as two writes: a
short header, then the queries. The server can't answer before the queries arrive, so it delays
its ACK of the header. Nagle holds the queries back until that ACK comes. Every small pipeline
takes ~41 ms (Linux's minimum delayed ACK): 1 connection, depth 16 does **~390 queries/s with
p50 41 ms**, against 235–255k queries/s with p50 0.05–0.07 ms once `TCP_NODELAY` is set. The driver
doesn't expose its socket, so after each connect the app sets `TCP_NODELAY` on every
socket the process has open (all of them are Skytable connections; `set_nodelay_on_all_sockets`
in [`main.rs`](app/src/main.rs)). `NODELAY=0` turns that off. Single queries are one write,
so Nagle doesn't affect them. `sky-bench` never pipelines.

- [`app/Dockerfile`](app/Dockerfile) builds with `rust:1.90-slim-bookworm` plus
  `libssl-dev` (the driver always links native-tls/OpenSSL, even for plain TCP), and the final
  image is `debian:bookworm-slim` + `libssl3` + the binary. No host Rust needed.
- Server: the same image and env-var configuration as [`../single-node`](../single-node)
  (`skytable/skytable:146d8664…`, skyd 0.8.4), with compose caps of 4 CPUs / 4 GB
  (`SKYTABLE_CPUS`, `SKYTABLE_MEM`), the same budget as single-node's `make benchmark`. The
  app client gets 4 CPUs (`APP_CPUS`) and reaches the server over the compose network.
- Compose project `skytable-session-store`, container `skytable-session-store`, so it runs next to
  `../single-node`.

## Sample output

2026-10-03, `make run` (defaults) on a running server, Docker Desktop 29.5.3 on an Apple M4 Pro
(Docker VM: 11 CPUs, 24.4 GB, aarch64), Skytable 0.8.4, server 4 CPUs / 4 GB, client 4 CPUs.
Nothing else was running in the Docker VM. The table below gives the range over three runs.

```text
1. schema        space gateway: sessions(token -> user_id, timestamps, hits, roles: [string], null ip),
                 quotas(api_key -> tokens: sint64), counters(name -> n: uint64)
2. load          200,000 sessions; one INSERT per round trip: 20,000 in 0.99 s = 20,140 rows/s
                 pipelines of 500, 1 connection:  90,000 in 0.13 s = 702,339 rows/s (35x)
                 pipelines of 500, 8 connections: 90,000 in 0.59 s = 151,455 rows/s (8x)
3. scaling       point SELECT on the primary key, random sessions, 2 s per row; latency per round trip
                 connections  depth    queries/s    p50 us    p99 us
                           1      1       28,683      28.6      97.9
                           4      1       77,363      46.8     133.8
                          16      1      247,947      59.2     168.1
                          64      1      352,776     160.9     435.0
                           1     16      255,092      55.4     132.5
                           4     16      709,490      80.1     200.0
                          16     16    1,658,235     141.7     465.9
                          64     16    1,901,736     517.0    1249.2
                           1     16          398   41006.3   41956.3   <- driver default, Nagle on (NODELAY=0)
4. request path  32 clients x 2,000 requests: select session, update last_seen + hits += 1, tokens -= 1 on one of 50 API keys (500 tokens each), read tokens
                 4 round trips: 83,993 requests/s, p50 350 us, p99 723 us per request
                   admitted 24,990, refused 39,010 (10 refused although a token was left: read after others' decrements);
                   balances off by a lost decrement: 0 of 50 keys; keys over their limit: 0
                 1 pipeline   : 264,640 requests/s (3.2x), p50 114 us, p99 232 us per request
                   admitted 25,000, refused 39,000 (0 refused although a token was left: read after others' decrements);
                   balances off by a lost decrement: 0 of 50 keys; keys over their limit: 0
5. counters      32 clients x 1,000 increments of one row (expected 32,000):
                 `n += 1` in the server:      n = 32,000 (0 lost) in 0.09 s
                 select n, then `n = n + 1`: n = 1,920 (30,080 lost updates) in 0.21 s
6. audit         INSPECT MODEL: 200,000 rows; sum(hits) over all sessions = 128,000 (requests served: 128,000), read with pipelined point SELECTs in 0.19 s

ALL CHECKS PASSED
```

Three consecutive runs (min – max):

| step | one query per round trip | pipelined |
|------|-------------------------:|----------:|
| load, 1 connection (rows/s) | 14,923 – 20,155 | 613,101 – 702,339 (pipelines of 500) |
| point SELECT, 1 connection (queries/s) | 21,651 – 28,683, p50 24–41 µs | 234,943 – 255,092 at depth 16, p50 51–67 µs per pipeline |
| point SELECT, 64 connections (queries/s) | 326,715 – 466,340 | 1,873,436 – 2,092,259 at depth 16 |
| best point SELECT setting | 466,340 (64 conns) | 2,092,259 (64 conns × 16) |
| request path, 32 clients (requests/s) | 79,439 – 99,921 | 204,611 – 264,640 |
| pipeline, driver default (Nagle on) | – | 396 – 398 queries/s, p50 41.0 ms |

In all three runs: no decrement lost on any key, no key over its limit, `sum(hits)` = 128,000,
and the atomic counter exactly 32,000. Read-modify-write ended at 1,916–1,947, so 94% of the
increments were lost.

An earlier set of three runs (2026-10-02), with other projects' containers using 2–3 CPUs of the
same VM, was 2–4x slower across the board (best pipelined setting 586,263 queries/s, best
unpipelined 230,162) and had p99s in the milliseconds; the ratios between the settings held.

## Where Skytable fits

- **Point reads and writes on a primary key.** Skytable allows nothing else (no secondary indexes,
  no ranges, no joins), and that is all a session or token lookup needs. A single round trip took
  24–41 µs at p50 here, and a 16-query pipeline 51–67 µs.
- **Typed rows, not just bytes.** Unlike a plain KV store, a session is a row with typed fields,
  a `null`able column and a list of roles. The server rejects a wrong type (error 109), and a
  `SELECT` returns only the columns asked for.
- **Updates in place, atomic per row.** `hits += 1` and `tokens -= 1` run inside the server, so
  32 clients hammering one row lose nothing. Doing the same from the client (read, then write)
  lost 94% of the increments. The rate limiter needs no locks or transactions, and its balances
  were exact.
- **Pipelining.** Batches of independent queries share one round trip. Insert throughput went up
  33–41x on one connection, and per-connection reads 8.5–12x. Pipelines give no atomicity or isolation (the docs
  say so): the rate limiter's balance read can include other clients' decrements, so a few
  requests (up to 12 per run) were refused while tokens were left, but none over the limit was ever
  admitted.
- **Multithreaded server.** Each connection is served concurrently, so throughput grows with
  connections. It reached ~1.9–2.1M point reads/s at 64 connections × depth 16 on 4 CPUs, and
  ~330–470k without pipelining. Going from 16 to 64 connections added only 10–15% (pipelined),
  so on 4 CPUs it levels off between them.

Not shown, because 0.8.4 doesn't have it: replication or clustering (see [`../README.md`](../README.md)),
TTLs (expiry has to be done by the application), or durable-on-ack writes. DML reaches disk within
the reliability service window (300 s by default).
