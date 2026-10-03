# TigerBeetle

Website: https://tigerbeetle.com/

Both examples run `client/demo.py` with the official Python client (accounts, transfers,
two-phase pending → post/void, a linked chain that fails atomically, a transfer rejected by
`debits_must_not_exceed_credits`) and use the built-in `tigerbeetle repl`.

- [`single-node/`](single-node) — one replica (`--replica-count=1`) on Docker Compose.
- [`docker-compose-cluster/`](docker-compose-cluster) — three replicas of one cluster, with a `make failover` that stops the primary.
- [`kubernetes-statefulset/`](kubernetes-statefulset) — three replicas as a plain StatefulSet on kind (one per zone), with an init container that formats (or recovers) the data file from the pod ordinal, a fixed ClusterIP per replica, Jobs for the demo, load and benchmark, and a `make failover` that kills the primary's pod. TigerBeetle has no Kubernetes operator or Helm chart of its own.

## Benchmark

`tigerbeetle benchmark`, 1M transfers (Apple M4 Pro, Docker VM aarch64, 2026-09-28): one replica
(4 CPUs / 6 GB) does 689k transfers/s at 26 ms batch p99; three replicas (2 CPUs / 4 GB each) do
381k/s at 50 ms p99 — quorum commit costs ~45% of throughput. Full tables and method:
[`docker-compose-cluster/README.md`](docker-compose-cluster/README.md#benchmark) and
[`single-node/README.md`](single-node/README.md#benchmark).

On kind (`kubernetes-statefulset/`, 2026-10-03) the same 1M-transfer benchmark does 347k transfers/s
at 55 ms batch p99. Killing the primary's pod under load stalled the client for at most 314 ms;
every acked transfer was in the balance afterwards.

## Known issues

- No official Kubernetes operator or Helm chart. The docs cover systemd, Docker and the managed
  service ([deploying](https://docs.tigerbeetle.com/operating/deploying/)). The
  `tigerbeetle.github.io/helm-charts` repo that Rafiki's docs point at returns 404. The community
  operators on GitHub (e.g. `Code-Growers/tigerbeetle-operator`, "experimental") have no users
  (0 stars, checked 2026-10-03).
- `--addresses` takes IP addresses only, no DNS names, and is read once at start. On
  Kubernetes that rules out the usual headless-Service pod DNS names, so
  `kubernetes-statefulset/` gives each replica a ClusterIP Service with a fixed IP.
- The replica count is fixed when the data files are formatted (`--replica-count`). Changing
  it means a new cluster. Scaling the StatefulSet to 4 would start `tigerbeetle-3`, whose
  `format --replica=3 --replica-count=3` is rejected (`src/tigerbeetle/cli.zig`; not run here).
