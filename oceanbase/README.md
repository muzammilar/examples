# OceanBase

Website: https://en.oceanbase.com/

The examples use the MySQL-mode user tenant `test` (port 2881); cluster-wide views are
read from the `sys` tenant.

- [`single-node/`](single-node) — one observer from the `oceanbase/oceanbase-ce` image (`MODE=mini`) on Docker Compose.

- [`docker-compose-cluster/`](docker-compose-cluster) — three observers in three zones (zone1–3) on Docker Compose,
  bootstrapped by hand without obd; tenant `test` has locality `F@zone1, F@zone2, F@zone3`, with a
  `make failover` that kills the leader's observer.

- [`htap-showcase/`](htap-showcase) — a Go program on one `mini` observer (port 2891): OLTP
  transactions and analytics at the same time on one hybrid row/column table (`WITH COLUMN
  GROUP(all columns, each column)`). It compares row-store and column-store scans, alone and
  next to concurrent writes, and shows that a column-store aggregate sees orders committed a
  moment earlier.

The cluster needs about 20 GB of Docker memory (3 × 6G `memory_limit` plus overhead) and ~20 GB of
disk. OceanBase's own recommendations for clusters are `obd` or
[ob-operator](https://github.com/oceanbase/ob-operator); ob-operator 2.3.4 refuses observers below
8Gi memory and 30Gi data + 30Gi redo storage each, which does not fit on a laptop.

## Benchmark

sysbench on one `mini` observer, 4 CPUs / 8 GB (memory raised from the usual 6 GB, because the observer's `memory_limit` floor is 6G; Apple M4 Pro, Docker VM aarch64, 2026-09-28): point selects 82k/s at p95 0.6 ms with 32 threads, `oltp_read_only` 4.6k tps (74k qps). `oltp_read_write` peaks at ~960 tps with 8 threads and halves at 32. Reads scale on the capped CPUs, while writes saturate early. Full table and method: [`single-node/README.md`](single-node/README.md#benchmark).

Cluster (3 observers, 2 CPUs / 7 GB each, all leaders in zone1, sysbench at 32 threads): point selects
40k/s, `oltp_read_write` ~900 tps. Killing the leader's observer moved leadership to zone2 and the
next write committed ~4.6 s after the kill. Full tables: [`docker-compose-cluster/README.md`](docker-compose-cluster/README.md#benchmark).

HTAP showcase (one `mini` observer, 6 CPUs, 2M orders in a hybrid row/column table, 2026-10-02): aggregations ran 5–170x faster through the column store than through the row copy of the same table. With 16 OLTP workers writing, 2 column-store analytics workers finished 3.6x more queries than row-store ones, and OLTP kept 74% of its solo ~1.5k tps (65% with row-store scans). Details: [`htap-showcase/README.md`](htap-showcase/README.md#sample-output).

## Known issues

Seen while building these examples (oceanbase-ce 4.4.2.1, 2026-10-02):

- obd refuses to start the observer when the Docker VM's `fs.aio-max-nr` is nearly used up,
  for example by a ScyllaDB container on the same VM. Raise it with
  `docker run --rm --privileged alpine sysctl -w fs.aio-max-nr=1048576`; it resets when Docker
  restarts.
- obd's disk check needs ~10 GB free in the Docker VM, so `make up` fails on a nearly full disk.
- In `MODE=mini`, a 2M-row load stalls at ~200k rows with the default memstore limit. The
  HTAP showcase sets `memstore_limit_percentage = 50`.
- The `test` tenant's 1.5G log disk stays ~78% full after a showcase run, and a second run in
  the same container loads ~10x slower (450 s vs 46 s). Use `make down up run`.
- Major compaction is slow on a laptop: ~6 min tenant-wide, 2.5–4 min for the 8 `orders`
  tablets.
- ob-operator 2.3.4 refuses observers below 8Gi memory and 30Gi data + 30Gi redo storage each.
