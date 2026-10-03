# TigerBeetle

Website: https://tigerbeetle.com/

| folder | what |
|--------|------|
| [`single-node/`](single-node) | One replica (`--replica-count=1`) on Docker Compose. |
| [`docker-compose-cluster/`](docker-compose-cluster) | Three replicas of one cluster; `make failover` stops the primary. |
| [`payments-ledger/`](payments-ledger) | Wallet/payments ledger in Rust (official client, built from source): 1M linked payment + fee transfers with overdraft protection, card holds (post/void/expire), linked currency exchange, idempotent retries, audit that debits equal credits. |
| [`cluster-operations/`](cluster-operations) | Changes to a running cluster, each under load: add/remove standbys (3 → 3 + 2 standbys → 3), standbys never form a quorum, replace a lost data file with `tigerbeetle recover`, resize the grid cache with a rolling restart. |
| [`kubernetes-statefulset/`](kubernetes-statefulset) | Three replicas as a plain StatefulSet on kind (one per zone): init container formats (or recovers) from the pod ordinal, fixed ClusterIP per replica, Jobs for demo/load/benchmark, `make failover` kills the primary's pod. |

`single-node` and `docker-compose-cluster` run `client/demo.py` with the official Python client
(accounts, transfers, two-phase pending → post/void, a linked chain that fails atomically, a
transfer rejected by `debits_must_not_exceed_credits`) and the built-in `tigerbeetle repl`.

## Benchmark

Apple M4 Pro, Docker VM aarch64, TigerBeetle 0.17.9. Each run lasts ~2 s, so treat these as rough.

| example | date | workload | result |
|---------|------|----------|--------|
| [single-node](single-node/README.md#benchmark) | 2026-09-28 | `tigerbeetle benchmark`, 1M transfers, 1 replica (4 CPUs / 6 GB) | 689k transfers/s, batch p99 26 ms |
| [docker-compose-cluster](docker-compose-cluster/README.md#benchmark) | 2026-09-28 | same, 3 replicas (2 CPUs / 4 GB each) | 381k transfers/s, batch p99 50 ms (quorum commit costs ~45%) |
| [kubernetes-statefulset](kubernetes-statefulset) | 2026-10-03 | same, 3 replicas on kind | 347k transfers/s, batch p99 55 ms; primary pod kill under load stalled the client ≤ 314 ms, every acked transfer kept |
| [payments-ledger](payments-ledger/README.md#sample-output) | 2026-10-02 | Rust client, 1 replica, no CPU limits, 1M payment + fee transfers in linked pairs, all fees to one hot account | 481k/s from 1 client (batch p50 11.7 / p99 44 ms), 533–858k/s from 2, 465k/s from 4; overdrafts rejected by the database, audit 0 mismatches |
| [cluster-operations](cluster-operations) | 2026-10-03 | 1 client at ~46k transfers/s during add/remove standbys, replace replica (`recover` + 4.6 s state sync), rolling restart | no failed request, no lost transfer; worst stall 1.9 s (rolling restart). `--cache-grid` 256 MiB → 2 GiB did not speed up the 1M benchmark (343k → 221k/s, single runs on a shared VM): the working set already fits. |

## Known issues

Seen with TigerBeetle 0.17.9, 2026-10-02.

- **Rust client not on crates.io.** The `tigerbeetle` crate there is a 0.0.1 placeholder from
  2023. The real client is in the main repo (`src/clients/rust`) and links a native `tb_client`
  library built with Zig from a release tag, with `-Dconfig-release` /
  `-Dconfig-release-client-min` matching the server, or the server rejects the client.
  `payments-ledger/app/Dockerfile` does this.
- **`payments-ledger` slows down on re-runs** against the same data file: ~675k/s and ~545k/s on
  the 2nd and 3rd runs, ~200k/s and ~174k/s on the 4th and 5th. `make down up` restores
  first-run numbers.
- **Replica count is fixed at format time** (`--replica-count`, at most 6). No in-place change
  from 3 to 5 (or 6 to 3) voting replicas: `reconfigure` rejects a different replica or standby
  count in 0.17.9 (`src/vsr.zig`), and no client or CLI exposes it. Changing it means a new
  cluster. On Kubernetes, scaling the StatefulSet to 4 would start `tigerbeetle-3`, whose
  `format --replica=3 --replica-count=3` is rejected (`src/tigerbeetle/cli.zig`; not run here).
- **Standbys are experimental** (`format --standby=<i>`): "standbys don't have a concrete
  practical use-case yet" (`src/tigerbeetle/cli.zig`). They cannot be promoted and do not count
  toward a quorum. Adding or removing one changes `--addresses`, so every replica needs a restart.
- **Lost data file:** bring the replica back with `tigerbeetle recover`, never `format`
  ([recovering](https://docs.tigerbeetle.com/operating/recovering/)).
- **No official Kubernetes operator or Helm chart.** The docs cover systemd, Docker and the
  managed service ([deploying](https://docs.tigerbeetle.com/operating/deploying/)). The
  `tigerbeetle.github.io/helm-charts` repo that Rafiki's docs point at returns 404. Community
  operators (e.g. `Code-Growers/tigerbeetle-operator`, "experimental") have 0 stars (checked
  2026-10-03).
- **`--addresses` takes IPs only**, no DNS names, and is read once at start. On Kubernetes that
  rules out headless-Service pod DNS names, so `kubernetes-statefulset/` gives each replica a
  ClusterIP Service with a fixed IP.
