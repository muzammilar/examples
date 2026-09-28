# OceanBase — single node

One OceanBase Community Edition observer from the official
[`oceanbase/oceanbase-ce`](https://github.com/oceanbase/docker-images/tree/main/oceanbase-ce)
image, as in the
[Docker quick start](https://en.oceanbase.com/docs/common-oceanbase-database-10000000001970931).
`MODE=mini` (the image default) runs `obd` inside the container to deploy one observer
with the smallest resource settings, then creates the MySQL-mode user tenant `test`.
Image `4.4.2.1-101000022026050611` (tag `4.4.2-lts`), multi-arch, so it runs natively on
Apple silicon.

```bash
make up       # start and wait for "boot success!" (about 1 minute here, up to 5)
make test     # run sql/*.sql: HASH/RANGE partitions, generated data, transaction, EXPLAIN pruning + PX plan, internal views
make status   # servers and tenants from the sys tenant
make cli      # interactive obclient as root@test
make cli-sys  # interactive obclient as root@sys (cluster-wide views)
make logs     # last 50 lines of the entrypoint (obd) output
make down     # remove the container and its data
```

- MySQL protocol: `localhost:2881` (`OB_PORT=... make up` to change the host port)
  - `mysql -h127.0.0.1 -P2881 -uroot@test` — user tenant `test`, no password
  - `mysql -h127.0.0.1 -P2881 -uroot@sys` — sys tenant (cluster administration)
- `sql/*.sql` run as `root@test`; `sql/*.sys.sql` run as `root@sys`, where the
  cluster-wide views (`DBA_OB_TENANTS`, `GV$OB_SERVERS`, `CDB_OB_TABLE_LOCATIONS`,
  unit configs) live.

## Memory and disk

Settings are in [`docker-compose.yml`](docker-compose.yml) and can be overridden with the
same variable names (`OB_MEMORY_LIMIT=... make up`).

| Setting | Value | Notes |
|---|---|---|
| `memory_limit` | 6G | image `mini` default; 4G fails creating the `test` resource pool, 5G creates the tenant but then stalls loading time zone data |
| `system_memory` | 1G | leaves 5G for tenants: `sys` 2G, `test` 3G |
| `datafile_size` | 2G | image default 5G |
| `log_disk_size` | 4G | image default 5G; `sys` and `test` get 2G each |

Give Docker Desktop at least 8 GB of RAM (OceanBase's stated minimum: 2 cores, 8 GB).
The container's resident memory was about 3.3–3.5 GiB after `make test`, but the observer
may grow to its 6G `memory_limit`. It also takes about 6.5 GB of disk inside the container
(the log disk and data file are preallocated) on top of the 1.9 GB image.

Bootstrap data lives in the container layer and is discarded by `make down`.
