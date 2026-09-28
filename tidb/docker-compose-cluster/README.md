# TiDB — minimal cluster with Docker Compose

A real (if small) TiDB cluster from the multi-arch `pingcap/pd`, `pingcap/tikv` and
`pingcap/tidb` images, all pinned to `v8.5.8`: 3 PD (placement driver: metadata, the
TSO timestamp oracle, region scheduling), 3 TiKV stores (data in Raft-replicated regions,
3 replicas each) and 1 stateless TiDB SQL node. A small `mysql` container (MariaDB's CLI)
runs the SQL. `make up` waits until all three stores are Up and every region has its
three replicas.

```bash
make up        # start PD -> TiKV -> TiDB (~30 s), wait for 3 stores Up and full replication
make test      # run sql/*.sql (cluster_info, AUTO_RANDOM, SPLIT TABLE / SHOW TABLE REGIONS, EXPLAIN ANALYZE cop tasks, transactions), then conflict/
make conflict  # two concurrent sessions: optimistic write conflict at COMMIT, pessimistic lock wait timeout
make status    # cluster_info, tikv_store_status, pd-ctl member leader / store
make cli       # interactive mysql client on TiDB
make down      # remove containers and volumes
```

- TiDB (MySQL protocol): `localhost:14000`, user `root`, no password (`TIDB_PORT` overrides)
- PD API: http://localhost:12379/pd/api/v1/stores (`PD_PORT` overrides)
- other versions: `TIDB_VERSION=v8.5.7 make up`

`sql/` in order:

| file | shows |
| --- | --- |
| `01-cluster.sql` | `TIDB_VERSION()`, `information_schema.cluster_info`, `tikv_store_status` |
| `02-schema-data.sql` | tables with `AUTO_RANDOM` keys, 500 customers and 20000 orders generated in SQL |
| `03-auto-random.sql` | the shard bits in generated ids and `LAST_INSERT_ID()` |
| `04-regions.sql` | `SPLIT TABLE ... REGIONS 8` with `tidb_scatter_region`, `SHOW TABLE ... REGIONS`, peers and leaders per store from `tikv_region_status` / `tikv_region_peers` |
| `05-explain.sql` | `EXPLAIN ANALYZE`: `cop[tikv]` operators pushed down to TiKV, `cop_task: {num: 8 ...}` one task per region |
| `06-transactions.sql` | pessimistic (`FOR UPDATE`) and optimistic transfers, a cross-region transaction, rollback |

PingCAP's quick start recommends [TiUP playground](https://docs.pingcap.com/tidb/stable/quick-start-with-tidb/)
for a local cluster, and its old Docker Compose deployment
([pingcap/tidb-docker-compose](https://github.com/pingcap/tidb-docker-compose)) is no longer
maintained. This example uses Docker Compose anyway so that nothing but Docker is needed and
every component is a pinned image.

Notes:
- `config/tikv.toml` shrinks TiKV for a laptop: 256 MB block cache per store (the default is
  45% of the Docker VM's memory, per store), no 5 GB reserved disk, and a 2 GB reported
  capacity. Without the last one, a Docker disk that is more than 80% full makes PD treat
  every store as low-space and it stops placing replicas.
- AUTO_RANDOM shard bits come from the transaction start timestamp, so all rows of one
  statement share a shard; `02-schema-data.sql` loads in 8 statements to spread them.
- TiKV logs warnings about kernel parameters (`somaxconn`, `swappiness`) and the
  `overlay` filesystem; they are harmless here.
- TiFlash (columnar replicas) is left out; it needs its own config and ~1 GB more memory.
  Prometheus/Grafana are left out too: every component serves Prometheus metrics
  (`pd:2379/metrics`, `tikv:20180/metrics`, `tidb:10080/metrics`) on the compose network
  if you want to add them.
