# kv-store

**A proof of concept for learning Rust and Raft, not a production store.**

A replicated key-value store. Nodes agree on every write with
[openraft](https://github.com/databendlabs/openraft), and each keeps its copy
in a write-ahead log on disk. A write is acknowledged once a majority of nodes
have it, so a three-node cluster keeps working with any one node down. A node
that comes back is caught up by the leader, even one that lost its disk.

## Testing

```bash
# Start a three-node cluster; returns once every node sees a leader
make up

# Write and read a key through the leader
make kv ARGS="put greeting hello"
make kv ARGS="get greeting"

# Show each node's role, term, log and snapshot position
make status

# Load-test the cluster: write and read throughput, with latency percentiles
make load

# Walk through failover, rebuilding a wiped node, and a load test (see demo.sh)
make demo

# Stop all services and remove their volumes; images and build caches stay
make down
```

`make load ARGS="--requests 10000 --concurrency 64"` passes options to the
load test. `make stop` keeps the nodes' data for the next `make up`, and
`make logs` follows them. The nodes are also on `localhost:7001`–`7003`:

```bash
curl localhost:7001/status
curl 'localhost:7002/kv/greeting?stale=true'   # that node's own copy
```

Writes and linearizable reads from the host get redirected to `kvN:7000`,
which only resolves inside the compose network. That's why `make kv` runs the
client in a container.

## How it works

```text
            client (kv CLI, curl)
                   │  PUT /kv/key
                   ▼
   ┌──────────┐  307 → leader  ┌──────────┐
   │ follower │ ─────────────► │  leader  │ ──── AppendEntries ───┐
   └──────────┘                └──────────┘                       ▼
        ▲                        WAL + fsync                ┌──────────┐
        └──────────── AppendEntries / InstallSnapshot ───── │ follower │
                                                            └──────────┘
```

openraft handles elections, replication and commitment. This crate supplies
the storage, the state machine, and JSON over HTTP for peers and clients.

### Write-ahead log

Each entry is one checksummed frame. A background task syncs appends, and
openraft counts an entry as durable once that `fsync` completes. On restart a
half-written frame at the end of the log is truncated, and the leader sends
that entry again. Damage anywhere else stops the node.

### Group commit

openraft 0.9 waits for each entry's `fsync` before taking the next write, so
the writer packs the writes that queue up meanwhile into one entry. Throughput
grows with the number of writes in flight (see [Benchmarks](#benchmarks)).

### Snapshots

Every `KV_SNAPSHOT_EVERY` entries, a node writes the whole map to disk and
purges the log it covers, keeping the last `KV_KEEP_LOGS` entries. A follower
further behind than that, or with no data at all, is sent the leader's
snapshot first.

### Wiping a disk

Raft assumes a node never loses what it has acknowledged. A wiped node has
forgotten its vote for this term, so it could vote twice and help elect two
leaders in one term. The build enables openraft's `loosen-follower-log-revert`
feature so the demo can rebuild a wiped node anyway. The demo never wipes kv1:
it has `KV_BOOTSTRAP` set, and with an empty disk it would form a new cluster.
In production, remove a node that lost its disk and add it back through a
membership change.

## Settings

| Variable | Default | Meaning |
|----------|---------|---------|
| `KV_ID` | | This node's ID |
| `KV_MEMBERS` | | Every member, as `1=host:port,2=host:port,...` |
| `KV_BOOTSTRAP` | `false` | Form the cluster on first start. Set on one node |
| `KV_LISTEN` | `0.0.0.0:7000` | Address to listen on |
| `KV_DATA_DIR` | `data` (`/data` in the image) | Where the log and snapshots live |
| `KV_SNAPSHOT_EVERY` | `1000` (`100` in compose) | Entries between snapshots |
| `KV_KEEP_LOGS` | `100` (`20` in compose) | Entries kept after a snapshot |

## Known limitations

`git grep Limitation` finds each of these in the code.

- Membership is fixed, and all traffic is plain HTTP with no authentication.
- The log and the whole key space are held in memory as well as on disk, and
  each snapshot is a full copy of the data.
- Purging old entries rewrites every entry that's kept; a real log would use
  segment files.
- Recovery assumes appends reach the disk in order. After a power cut (not a
  process crash) the unsynced tail can come back with a hole in it, which
  replay reports as corruption.
- A wiped node forgets its vote, and a wiped bootstrap node forms a new
  cluster (see [Wiping a disk](#wiping-a-disk)).
- Writes are at-least-once: one that times out may still be applied, and a
  retried `put` can land after a newer write to the same key.

## Development

```bash
make test      # unit tests, openraft's storage suite, and in-process 3-node clusters
make bench     # write throughput through one node, 1 vs 16 vs 64 writes in flight
make lint      # rustfmt and clippy (pedantic)
make quality   # cargo-deny, cargo-machete, hadolint, shellcheck
make check     # all of the above but bench
```

`make quality` runs
[cargo-deny](https://github.com/EmbarkStudios/cargo-deny) (policy in
`deny.toml`), [cargo-machete](https://github.com/bnjbvr/cargo-machete),
[hadolint](https://github.com/hadolint/hadolint) and
[shellcheck](https://www.shellcheck.net), each in its own container.

### Benchmarks

On a laptop, `make load` against the compose cluster:

```text
2000 requests of each kind, 32 in flight, 100-byte values
put     11841 ops/s   p50   2.4ms   p95   4.4ms   p99   7.2ms   max   7.4ms
get     25422 ops/s   p50   0.6ms   p95   2.8ms   p99  30.2ms   max  36.1ms
```

and `make bench` ([criterion](https://github.com/bheisler/criterion.rs)),
through one in-process node:

| Writes in flight | Throughput |
|------------------|------------|
| 1 | ~2,400 writes/s |
| 16 | ~19,000 writes/s |
| 64 | ~62,000 writes/s |

### Layout

| Path | What's there |
|------|--------------|
| `src/storage/` | Write-ahead log, state machine, snapshots |
| `src/server/` | Node lifecycle, HTTP API, and the write batcher |
| `src/network.rs` | Raft RPCs to peers |
| `src/client.rs`, `src/bin/kv/` | Client library and CLI, including the load test |
| `tests/storage_suite.rs` | openraft's storage compliance suite, run against our storage |
| `tests/cluster.rs` | Replication, failover, restart, rebuild and batching tests on real clusters |
| `benches/writes.rs` | Single-node write throughput |
