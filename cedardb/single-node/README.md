# CedarDB — single node

One [CedarDB](https://cedardb.com/docs/get_started/install_with_docker/) Community Edition
server (`cedardb/cedardb`), the HTAP database from the Umbra team at TUM. It speaks the
PostgreSQL wire protocol, so the stock `psql` client (a `postgres:17-alpine` tools
container, since the image ships only `pg_isready`) drives it.

```bash
make up       # start and wait for pg_isready, print version()
make test     # run sql/*.sql: generate 100k customers + 3M orders with generate_series/random(),
              # join/GROUP BY, percentiles, window functions, EXPLAIN / EXPLAIN ANALYZE,
              # committed + rolled-back transactions and a bulk UPDATE, CSV export + csvview, vector distance
make status   # container state and on-disk size per table
make cli      # interactive psql
make down     # remove the container and its volume
```

- PostgreSQL protocol: `localhost:5434` (host port 5434 to avoid a local PostgreSQL on 5432;
  override with `CEDARDB_PORT=5432 make up`)
- User `postgres`, database `postgres`, fixed demo password `Cedar-Demo-1`
  (`PGPASSWORD=Cedar-Demo-1 psql -h localhost -p 5434 -U postgres`). CedarDB rejects weak
  passwords: 8+ characters with upper, lower, digit and symbol.

The image is multi-arch (amd64 + arm64) and runs natively on Apple silicon. No license key
is needed: without one CedarDB runs as the free Community Edition, limited to 64 GiB of
data (beyond that it turns read-only) and without Enterprise features; see
https://cedardb.com/docs/licensing/. CedarDB is single-node only; there is no cluster
or replication mode to demo. By default it sizes its buffer and work memory to 45% of
the Docker VM's memory each.
