# RonDB — cluster with Docker Compose

The minimal RonDB (MySQL NDB Cluster fork by Hopsworks) cluster that
[rondb-docker](https://github.com/logicalclocks/rondb-docker) builds, written out as a plain
compose file:

| service  | process                            | node id | role                                                    |
|----------|------------------------------------|---------|---------------------------------------------------------|
| `mgmd`   | `ndb_mgmd`                         | 65      | management server: hands out [`config/config.ini`](config/config.ini), arbitrator |
| `ndbd-1` | `ndbmtd`                           | 1       | data node, node group 0                                 |
| `ndbd-2` | `ndbmtd`                           | 2       | data node, node group 0 (`NoOfReplicas=2`: a replica of every fragment) |
| `mysqld` | `mysqld`                           | 67      | MySQL Server, SQL on top of the NDB tables              |
| `rest`   | `rdrs2`                            | 195     | REST API server: pk-read / batch / scan over HTTP via the NDB API |

```bash
make up        # start in order (mgmd -> data nodes -> mysqld -> rest), ~40 s
make test      # sql/*.sql: ENGINE=NDB table, fragments and replicas per data node (ndbinfo),
               # a transaction + rollback, pk lookup vs table scan (EXPLAIN + NDB API counters);
               # then ndb_desc -pn and a REST API pk-read with curl
make failover  # stop data node 2: SQL + REST keep working on node 1; restart node 2 (node restart)
make status    # ndb_mgm -e show, all report memory
make cli       # interactive mysql client (root) on the MySQL Server
make down      # remove containers and the data nodes' volumes
```

- MySQL: `localhost:3307`, user `rondb` / `rondb` (override the port with `RONDB_MYSQL_PORT`);
  inside the container `mysql -uroot` has no password.
- REST API: `http://localhost:4406/0.1.0/<db>/<table>/pk-read` (override with `RONDB_REST_PORT`), e.g.
  ```bash
  curl -X POST localhost:4406/0.1.0/demo/accounts/pk-read -H 'Content-Type: application/json' \
    -d '{"filters": [{"column": "id", "value": 3}], "readColumns": [{"column": "balance"}]}'
  ```
  It runs with `Security.InsecureAllowAll` (no API keys, no TLS). At startup it keeps logging
  `[FS Cache Event] Failed to get feature_view table` retries: that is the Hopsworks
  feature-store cache looking for tables this cluster does not have, and harmless.
- Image `hopsworks/rondb:26.02.10` (26.02 LTS, amd64 + arm64; override with `RONDB_VERSION`).
  Every service runs as uid 1000 like rondb-docker (mysqld will not run as root).
- Memory follows rondb-docker's `mini` profile (`TotalMemoryConfig=2100M`, `NumCPUs=2` per data
  node, ~6 GB for the whole cluster). The redo log is shrunk and the disk-data tablespace left out.
- Data nodes start with `--initial` only when their volume is empty, so the restart in
  `make failover` is a real node restart ("Not initial start"): local recovery, then copy the
  changes made meanwhile from node 1. mysqld keeps its data dictionary in its container layer and
  initializes it on first start; recreate rather than restart it.
- `ndb_mgm` and `ndb_desc` run in the `mgmd` container; `ndb_desc`/other NDB API tools use the
  spare `[API]` slots 231–232 in `config.ini`.
