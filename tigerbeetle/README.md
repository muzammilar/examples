# TigerBeetle

Website: https://tigerbeetle.com/

Both examples run `client/demo.py` with the official Python client (accounts, transfers,
two-phase pending → post/void, a linked chain that fails atomically, a transfer rejected by
`debits_must_not_exceed_credits`) and use the built-in `tigerbeetle repl`.

- [`single-node/`](single-node) — one replica (`--replica-count=1`) on Docker Compose.
- [`docker-compose-cluster/`](docker-compose-cluster) — three replicas of one cluster, with a `make failover` that stops the primary.
- [`cluster-operations/`](cluster-operations) — what changes on a running cluster, each step under load: adding and removing standby replicas (3 → 3 + 2 standbys → 3), showing that standbys never form a quorum, replacing a replica's lost data file with `tigerbeetle recover`, and resizing the grid cache with a rolling restart.

## Benchmark

`tigerbeetle benchmark`, 1M transfers (Apple M4 Pro, Docker VM aarch64, 2026-09-28): one replica
(4 CPUs / 6 GB) does 689k transfers/s at 26 ms batch p99; three replicas (2 CPUs / 4 GB each) do
381k/s at 50 ms p99 — quorum commit costs ~45% of throughput. Full tables and method:
[`docker-compose-cluster/README.md`](docker-compose-cluster/README.md#benchmark) and
[`single-node/README.md`](single-node/README.md#benchmark).

Cluster operations under load (`cluster-operations/`, 2026-10-03, one client at ~46k transfers/s):
adding two standbys, removing them, replacing a lost replica (`recover` + 4.6 s state sync) and a
rolling restart all completed without a failed request or a lost transfer. The worst single-request
stall was 1.9 s, during the rolling restart. Raising `--cache-grid` from 256 MiB to 2 GiB did not
speed up the 1M-transfer benchmark (343k → 221k transfers/s in single runs on a shared VM): its
working set already fits.

## Known issues

- The number of active replicas is fixed at format time (`--replica-count`, at most 6). There is
  no way to go from 3 to 5 (or 6 to 3) voting replicas in place. The `reconfigure` operation in
  the protocol rejects a different replica or standby count in 0.17.9 (`src/vsr.zig`), and no
  client or CLI exposes it. Changing the replica count means a new cluster.
- Standby replicas (`format --standby=<i>`) are experimental: "standbys don't have a concrete
  practical use-case yet" (`src/tigerbeetle/cli.zig`). They cannot be promoted, and they do not
  count toward a quorum. Adding or removing one changes `--addresses`, so every replica needs a
  restart.
- A replica that lost its data file must come back with `tigerbeetle recover`, never `format`
  ([recovering](https://docs.tigerbeetle.com/operating/recovering/)).
