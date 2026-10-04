# RisingWave — order metrics from Postgres CDC

Incremental streaming SQL, the workload RisingWave is built for: an OLTP Postgres database
(customers, products, orders) is replicated into RisingWave with the built-in `postgres-cdc`
connector, page-view events are written into RisingWave directly, and two materialized views (a
3-way join + aggregate, and a funnel joining events with orders) stay current while Postgres takes
~20k transactions/s. The Go program measures how fresh the MVs are and compares with re-running
the same query on Postgres every few seconds, then checks the MV against Postgres.

## Quick start

```bash
make up      # Postgres (wal_level=logical) + RisingWave single_node, wait until healthy
make run     # build app/ and run it (about 2 min); output also in results/
make status  # streaming jobs, CDC progress, replication slot lag
make cli     # psql on RisingWave;  make cli-postgres: psql on Postgres
make down    # remove containers, volumes and the app image
```

`make run` needs empty tables: `make down up run` for a second run.

## Setup

| Service | Image | Port | Role | Limits |
|---|---|---|---|---|
| `postgres` | `postgres:17-alpine` | `127.0.0.1:5433` | OLTP database `shop`, `wal_level=logical` ([`postgres/init.sql`](postgres/init.sql)) | 4 CPUs, 2 GB |
| `risingwave` | `risingwavelabs/risingwave:v3.1.0` | `127.0.0.1:4566` | `single_node` (see [`../single-node`](../single-node)) | 4 CPUs, 8 GB |
| `app` (profile `app`) | built from [`app/`](app) (Go 1.26, `jackc/pgx` v5.11.0) | — | load, probes, check | 2 CPUs |

| Variable | Default | Meaning |
|---|---|---|
| `SEED_ORDERS` | 1,000,000 | orders in Postgres before CDC starts (10,000 customers in 8 regions, 1,000 products in 20 categories) |
| `DURATION` | 30 | seconds per phase |
| `OLTP_WORKERS` | 8 | concurrent Postgres connections running single-statement transactions |
| `EVENT_WORKERS` × `EVENT_RATE` | 2 × 2,500 rows/s | page views inserted into RisingWave in 100-row batches |
| `POLL_INTERVAL` | 5 | seconds between re-runs of the query on Postgres (phase 2) |

## What it does

1. Seed Postgres. In RisingWave: `CREATE SOURCE shop_pg WITH (connector = 'postgres-cdc', ...)`
   and `CREATE TABLE customers / products / orders (...) FROM shop_pg TABLE 'public.<t>'` (initial
   snapshot, then the WAL stream); `page_views` as an append-only table.
2. Materialized views:
   - `sales_by_region_category`: orders ⋈ customers ⋈ products, `count(*)` and
     `sum(qty * price_cents)` per region and category, cancelled orders excluded.
   - `product_funnel`: page views per product joined with non-cancelled orders per product.
3. OLTP mix on Postgres: 60% new order, 25% ship (`paid` → `shipped`), 10% cancel (removes the
   order from the MV), 4.9% change quantity, 0.1% move a customer to another region (moves ~100
   orders between MV groups).
4. Phase 1: OLTP + page views + a probe: every second it commits an order for a dedicated
   customer/product in Postgres, then polls the MV every 10 ms until the order is counted. A reader
   also selects all MV rows every 100 ms.
5. Phase 2: the same OLTP, and the same aggregate query run on Postgres every 5 s; probe freshness =
   commit until the end of the first poll whose result includes it.
6. Check: the MV equals the query on Postgres (all groups, counts and sums), `sum(views)` equals
   the acknowledged page views, and the `orders` row counts match. Exit 1 otherwise.

## Results

2026-10-04, Apple M4 Pro, Docker VM aarch64 (11 CPUs, 24 GB), limits as in Setup, defaults, one run.

| Step | Result |
|---|---|
| seed 1M orders in Postgres | 13.5 s |
| CDC snapshot of 1M orders into RisingWave | 4.5 s (220k rows/s) |
| `CREATE MATERIALIZED VIEW sales_by_region_category` (backfill over 1M orders) | 3.1 s |
| `CREATE MATERIALIZED VIEW product_funnel` | 2.1 s |

| | Phase 1: RisingWave MV over CDC | Phase 2: query on Postgres every 5 s |
|---|---|---|
| Postgres OLTP | 20,649 tx/s, p50 0.3 ms, p99 1.7 ms | 20,558 tx/s, p50 0.3 ms, p99 1.5 ms |
| freshness (commit → visible), p50 / p99 | **1.01 s / 2.03 s** (14 probes) | 4.00 s / 4.05 s (6 probes) |
| reading the aggregate | MV read p50 5.8 ms, p99 59 ms (under load; 1.7 ms idle) | query p50 169 ms, p99 187 ms (under load; 78 ms idle) |
| other load | 4,980 page views/s into RisingWave | — |

Check after the load: 161 groups, 1,531,681 non-cancelled orders, revenue 24,101,435,514 cents;
0 differing groups (converged 1.5 s after the load stopped); 149,400 page views acknowledged = 149,400
in the funnel; 1,741,151 order rows in both systems. `OK`.

- Freshness in RisingWave is bounded by the barrier interval (`barrier_interval_ms` 1000,
  `checkpoint_frequency` 1): a change becomes visible in the MV at the next checkpoint, 1–2 s after
  the Postgres commit.
- Re-running the query on Postgres takes 169 ms per run at 1.7M orders (a full join and scan, so it grows with the
  tables); freshness is the poll interval plus the query time. Polling every 5 s did not slow the
  OLTP load here (one 4-CPU Postgres, one query at a time).

### Design notes

- The MV is maintained per change: an order insert, a status change or a cancel updates one group;
  moving a customer retracts and re-adds that customer's orders through the join state.
- Joins keep both sides as state in Hummock (LSM on the local filesystem here, S3/MinIO in a
  cluster). Reading the MV is a primary-key scan of 161 rows.
- The CDC tables are snapshotted and then switched to the replication stream without a gap; the
  final check compares full results, so a missed or double-applied change would show up.
- Events go into RisingWave over the Postgres protocol (no Kafka); in production they would come
  from a Kafka/Redpanda source.

## Known issues

- `CREATE TABLE ... FROM <cdc source>` returns before the snapshot is loaded; the app polls
  `count(*)` until all seeded rows are there.
- After the run the replication slot `rw_shop` showed 577 MB between `pg_current_wal_lsn()` and
  `confirmed_flush_lsn`: Postgres keeps that WAL until RisingWave confirms it. Watch slot lag on a
  busy database.
- Freshness probes run one at a time with a 1 s pause, so a 30 s phase gives only 14 (RisingWave)
  and 6 (Postgres) samples.

## Links

- [PostgreSQL CDC](https://docs.risingwave.com/ingestion/sources/postgresql/pg-cdc)
- [CDC with RisingWave](https://docs.risingwave.com/ingestion/cdc-with-risingwave)
