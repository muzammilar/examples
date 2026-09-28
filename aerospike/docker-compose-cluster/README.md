# Aerospike Community Edition — 3-node cluster with Docker Compose

Three CE nodes joined over mesh heartbeats ([`aerospike.conf`](aerospike.conf)),
namespace `test` with `replication-factor 2` in memory. Each node has an
[aerospike-prometheus-exporter](https://github.com/aerospike/aerospike-prometheus-exporter)
next to it; Prometheus scrapes all three and Grafana shows a small dashboard.

```bash
make up       # start and wait until the cluster is stable at size 3
make test     # run aql/test.aql (via aerospike-tools): insert, select, delete, SHOW SETS
make failover # stop aerospike-3: size 2, all records readable/writable; restart, wait for migrations
make benchmark # asbench: insert, read-only and 80/20 read/update (SMOKE=1 for a 10 s run)
make status   # asadm info
make cli      # interactive asadm
make down     # remove containers
```

`make failover` first truncates set `test.failover` (so it can be rerun), then writes [`failover/*.aql`](failover) records, stops `aerospike-3` and waits for the
cluster to re-form at size 2: with RF=2 every partition still has one copy, so the surviving replicas
become masters and all reads and writes keep working. It then starts the node again and waits for
`cluster-stable:size=3;ignore-migrations=false` (migrations finished).

- Client: `localhost:3000` (aerospike-1)
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3001 (anonymous admin) → **Aerospike** dashboard
- `make up` also fetches the Cluster, Node, Namespace, Set and Latency dashboards from
  [aerospike-monitoring@v3.20.0](https://github.com/aerospike/aerospike-monitoring/tree/v3.20.0/config/grafana/dashboards)
  into the gitignored `grafana/provisioning/dashboards/upstream/` → Grafana folder **upstream**.
  Panels for device/pmem storage, strong consistency and alerts stay empty in this setup.

`SHOW SETS` in the test output lists the remaining record on two of the three nodes (RF=2).
The OS-tuning warnings in the server log (THP, swappiness, min-free-kbytes) are expected
in containers.

## Benchmark

`make benchmark` runs [`asbench`](https://aerospike.com/docs/database/tools/asbench/) from the
`aerospike/aerospike-tools:13.1.0` image ([`bench/run.sh`](bench/run.sh)) against set `test.bench`,
which it truncates first:

| workload | asbench | what it measures |
|---|---|---|
| `insert` | `-w I` | writes `KEYS` new records (100-byte blob bin), each to master + replica (RF=2) |
| `read` | `-w RU,100 -t DURATION` | random single-record reads over those keys |
| `read-update` | `-w RU,80 -t DURATION` | 80 % reads, 20 % updates |

Every request is a single-record operation that the client sends straight to the partition's
master node (the client knows the partition map), and namespace `test` is in memory, so this is
Aerospike's low-latency key-value path: no coordinator hop, no disk. Writes wait for the replica
(`commit-level all`), which is why they cost several times a read.

```bash
make benchmark                           # 1M keys, 60 s per timed workload, 16 threads
make benchmark SMOKE=1                   # 100k keys, 10 s
make benchmark DURATION=120 THREADS=64 KEYS=5000000
```

It prints a summary table (ops/s, mean, p50/p95/p99/p99.9 latency, errors + timeouts per op)
and keeps the raw asbench output (including the cumulative HDR histograms) and a parsed JSON with
the server/tool versions, cluster size, RF, parameters and Docker VM CPUs/memory in the gitignored
`results/aerospike-<UTC time>.{txt,json}`. The parser, [`bench/report.py`](bench/report.py), is
standard-library Python run with `uv run --frozen` in the `ghcr.io/astral-sh/uv` image.

All three nodes, the client and the exporters share one Docker VM, so the numbers show relative
costs (read vs. write vs. replicated write), not what the hardware can do. To push further, raise
`THREADS` until p99 climbs, run more than one bench container, or give the nodes their own hosts.

### Sample results

TODO: numbers from a run on a quiet machine (`make benchmark`, defaults).

| workload | op | ops/s | p50 ms | p95 ms | p99 ms | p99.9 ms |
|---|---|---|---|---|---|---|
| insert | write | TODO | TODO | TODO | TODO | TODO |
| read | read | TODO | TODO | TODO | TODO | TODO |
| read-update | read | TODO | TODO | TODO | TODO | TODO |
| read-update | write | TODO | TODO | TODO | TODO | TODO |
