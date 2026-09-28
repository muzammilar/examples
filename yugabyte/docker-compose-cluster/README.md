# YugabyteDB — 3-node cluster with Docker Compose

Three `yugabyted` nodes, each in its own simulated zone (`docker.local.zone1..3`): `yb-1`
starts the universe, `yb-2` and `yb-3` join it with `--join=yb-1`, and `make up` runs
`yugabyted configure data_placement --fault_tolerance=zone` so every tablet has one of its
three replicas (RF=3) per zone. Prometheus scrapes each node's `/prometheus-metrics`
(yb-master `:7000`, yb-tserver `:9000`, YCQL `:12000`, YSQL `:13000`) for Grafana.

```bash
make up        # start; nodes join one after another (~30 s), then zone-aware placement
make test      # run ysql/*.sql (sharding, tablet leaders/replicas, transaction, index) and ycql/*.cql (TTL, JSONB, transactional table + index)
make failover  # stop yb-3: YSQL + YCQL keep working after leader re-election; restart it
make status    # yugabyted status, yb-admin list_all_masters / list_all_tablet_servers
make cli       # interactive ysqlsh on yb-1
make cli-ycql  # interactive ycqlsh on yb-1
make down      # remove containers (data lives inside them)
```

`make failover` stops `yb-3` and runs [`failover/`](failover) YSQL and YCQL writes and reads on
`yb-1`: with RF=3 the tablets it led elect new leaders on `yb-1`/`yb-2` within seconds, and
`yb-admin` shows its master `TIMED_OUT` and its tserver's heartbeat delay growing. It then starts
`yb-3` again and waits until its master and tserver are `ALIVE`.

- YSQL: `localhost:5433`, YCQL: `localhost:9042` (yb-1)
- yugabyted UI: http://localhost:15433
- yb-master UI: http://localhost:7000, yb-tserver UI: http://localhost:9000 (yb-1)
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000 (anonymous admin) → **YugabyteDB** dashboard
- `make up` also fetches the official YugabyteDB dashboard from
  [yugabyte-db@v2026.1.2.0/cloud/grafana](https://github.com/yugabyte/yugabyte-db/tree/v2026.1.2.0/cloud/grafana)
  into the gitignored `grafana/provisioning/dashboards/upstream/` → Grafana folder **upstream**.
  `prometheus/prometheus.yml` adds the `node_prefix`/`export_type` labels and the
  `handler_latency_*` → `rpc_latency{saved_name=...}` relabeling it expects. Its YEDIS panels stay empty.

The image is multi-arch and runs natively on Apple silicon. It runs as uid 10001, so data
stays in each container's writable layer (a fresh named volume would be root-owned).
On macOS, AirPlay Receiver listens on port 7000; turn it off or remap to `7001:7000` if
the port is taken. Tablet leaders start unevenly spread and the load balancer evens them out
over a few minutes. For alerting see
[YugabyteDB Anywhere](https://docs.yugabyte.com/stable/yugabyte-platform/) or the
[Prometheus integration docs](https://docs.yugabyte.com/stable/explore/observability/prometheus-integration/).
