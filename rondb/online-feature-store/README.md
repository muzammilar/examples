# RonDB — online feature store (Go)

Feature-vector serving, the workload RonDB runs behind [Hopsworks](https://www.hopsworks.ai/):
batched primary-key reads over SQL and REST on in-memory, replicated tables, timed by a Go client
([`client/main.go`](client/main.go), `go-sql-driver/mysql` + `net/http`).

## Quick start

```bash
make up        # cluster + client image (~1 min; host ports: MySQL 3308, REST 4407)
make run       # load 1M users x 2 feature groups if not loaded (~15 s), then all five modes (~2 min)
make run BATCH=1 MODES=sql-in,rest-batch CONCURRENCY=32 DURATION=30s
make failover  # serve while data node 2 is stopped and restarted
make status    # ndb_mgm show + memory report
make cli       # mysql client on database fs
make down      # remove containers, data-node volumes and the client image
```

## Setup

Same layout as [`../docker-compose-cluster`](../docker-compose-cluster): 1 management server,
2 data nodes in one node group (`NoOfReplicas=2`), 1 MySQL Server, 1 REST API server `rdrs2`.

| item | value |
|---|---|
| Compose project | `rondb-feature-store`, containers `rondb-fs-*` (runs next to `../docker-compose-cluster`) |
| `rdrs2` threads | 8 instead of 4 (with 4 it was the bottleneck for `rest-batch`) |
| Loader | retries NDB temporary errors (MySQL 1297; the small redo log answers bulk inserts with error 410 "REDO log files overloaded" until a checkpoint catches up) |
| Client | `CLIENT_CPUS=4` |
| Data | `fs.user_profile_1`, `fs.user_activity_1` (Hopsworks `<name>_<version>` naming), `ENGINE=NDB`, `PRIMARY KEY (user_id) USING HASH` (hash index only), 8 features each, 1M users each; 2M rows ~160 MiB `DataMemory` per data node, both hold every row |
| Request | one feature vector = features of `BATCH` (16) random distinct users from both groups (32 rows); `CONCURRENCY` (16) clients back to back for `DURATION` (20 s) per mode after a 3 s warm-up; a vector counts only if all 32 rows return |

| mode | per feature vector |
|---|---|
| `sql-single` | 32 prepared `SELECT ... WHERE user_id = ?` through `mysqld`, one after another |
| `sql-in` | 2 prepared `SELECT ... WHERE user_id IN (16 ids)`, one per feature group: `mysqld` turns each into one batch of key reads (MRR) |
| `sql-join` | 1 `SELECT` joining both groups on `user_id`, `IN (16 ids)`, pushed down to the data nodes as one join (`EXPLAIN`: "pushed join") |
| `rest-pk` | 32 `POST /0.1.0/fs/<table>/pk-read` to `rdrs2` |
| `rest-batch` | 1 `POST /0.1.0/batch` carrying the 32 pk-reads: `rdrs2` sends them to the data nodes as one NDB API batch, no SQL layer |

`make failover` serves for 90 s, half the clients `sql-in` and half `rest-batch`, `docker stop`s
data node 2 at ~15 s and starts it 30 s later. Per second it prints vectors served, served after a
retry, failed (up to 3 retries, 10 ms apart), and the slowest request.

## Results

2026-10-02, Docker Desktop 29.5.3 on Apple M4 Pro (Docker VM: 11 CPUs, 24.4 GB, aarch64, native
arm64 images), RonDB 26.02.10, 2 data nodes (`NumCPUs=2` each), NoOfReplicas=2, no CPU limits,
client 4 CPUs. The VM was shared with other stacks; these are the quietest runs. Under heavier
neighbour load `make run` gave 4.4k vectors/s for both `sql-in` and `rest-batch` (p99 17-19 ms),
and the batched modes stayed 6-10x ahead of the one-row-per-request ones.

`make run` (16 users per vector, 16 clients):

```
mode         vectors/s      rows/s   p50 ms   p99 ms  p99.9 ms  errors
sql-single        1190       38066    11.39    44.47     70.59       0
sql-in            8942      286144     1.44     6.67     14.76       0
sql-join          3053       97707     4.04    21.56     52.24       0
rest-pk            762       24381    17.68    65.75    117.32       0
rest-batch        7065      226066     1.71     9.65     20.41       0
```

`make run BATCH=1` (1 user per vector = 2 rows):

```
mode         vectors/s      rows/s   p50 ms   p99 ms  p99.9 ms  errors
sql-single       19081       38162     0.67     3.42      8.65       0
sql-in           18490       36980     0.69     3.56      9.23       0
sql-join         16791       33581     0.65     5.08     15.07       0
rest-pk          11707       23415     0.99     7.31     16.53       0
rest-batch       28378       56755     0.43     2.56      6.38       0
```

`make failover` (excerpt):

```
UTC         s  sql-in ok/retr/fail max    rest-batch ok/retr/fail max
05:25:41   14    3017/  0/  0    11ms    3132/  0/  0    11ms
==> 05:25:42 docker stop rondb-fs-ndbd-2
05:25:42   15    2074/  8/  0   223ms    1919/  0/  0    20ms
05:25:43   16    5934/  0/  0    19ms     392/  0/  0  1296ms
05:25:44   17    2268/  0/  0    40ms    2216/  0/  0    27ms
...
==> 05:26:13 docker start rondb-fs-ndbd-2
05:26:12   45    1368/  0/  0    42ms    1381/  0/  0    42ms
...
==> 05:26:35 data node 2 started (healthy) again
...
sql-in     total: 168092 vectors served, 8 needed a retry, 0 failed
rest-batch total: 166237 vectors served, 0 needed a retry, 0 failed
distinct errors seen (before retry):
  first at  15s  sql-in: Error 1205 (HY000): Lock wait timeout exceeded; try restarting transaction
```

## Findings

- **Batching.** The same 32 rows cost 11-18 ms as 32 round trips and 1.4-1.7 ms as one batch:
  `sql-in` and `rest-batch` serve 7-9x more vectors at ~7x lower p99 than `sql-single` / `rest-pk`.
  Each read in a batch is a hash lookup on the node owning the key, run in parallel across both
  data nodes and their LDM threads.
- **Small vectors: REST is the shortest path.** One user per vector: `rest-batch` has the lowest
  latency (0.43 ms p50, 2.6 ms p99) and highest throughput (28k vectors/s), no SQL parsing or
  `mysqld` hop. At 16 users `sql-in` pulls ahead: `rdrs2` spends its CPU on JSON for 32
  sub-operations (~2.2 CPUs, the busiest container in that mode; data nodes ~0.5 CPU each).
- **Joins.** The pushed-down join is the fastest SQL form for one user, but for 16 users it plans
  as a scan of every fragment (`range` on `p0..p3` with MRR): 4 ms p50, slower than two `IN` lists.
- **Failover.** Each data node holds a replica of every row, so stopping one only aborts its
  in-flight transactions: 8 `sql-in` reads got error 1205 and succeeded on retry, `rest-batch` saw
  one ~1.3 s stall and no errors, nothing failed. The restarted node recovered from its own
  checkpoint and caught up from node 1 in ~22 s while serving continued (the dips around the
  restart are the copy competing for the same CPUs).

## Known issues

- `rdrs2`'s Hopsworks feature-store endpoints (`POST /0.1.0/feature_store`,
  `/0.1.0/batch_feature_store`; take a feature view name and entry keys) read metadata from the
  `hopsworks` database that only a Hopsworks installation creates. Here they answer
  `Database/Table does not exist. Database: hopsworks. Table: feature_store`. They resolve the
  feature view, then issue the same batched pk-reads as `rest-batch`, so `rest-batch` is what is
  measured.
