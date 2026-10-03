# TimescaleDB — fleet telemetry (Rust)

Electric delivery vehicles report position, speed, battery and motor temperature every 30 s.
Fleet dashboards need two things: today's numbers for each fleet and region, and history that
stays cheap to keep. This example shows where TimescaleDB is strong on that workload: time series
**inside full Postgres**. Telemetry sits in a hypertable next to ordinary `fleets` and `vehicles`
tables, and dashboards join them in plain SQL. Old chunks go to the columnstore, and continuous
aggregates answer the dashboards.

The client ([`client/src/main.rs`](client/src/main.rs)) is Rust: `tokio-postgres`, binary
`COPY ... FROM STDIN BINARY` through `BinaryCopyInWriter`, `rand` for the simulation. It runs
against one TimescaleDB server (`timescale/timescaledb-ha:pg18.6-ts2.30.2`, as in
[`../single-node`](../single-node)), capped at 4 CPUs / 6 GB in compose.

- **Data.** 20 fleets in 5 regions, 1000 vehicles (4 models). A vehicle drives during its shift
  (smoothed random speed, a heading random walk for lat/lon, odometer, battery drain), parks and
  charges outside it, and now and then overheats for 30 minutes. One reading per vehicle every
  `INTERVAL_S` (30) s for `DAYS` (5) days, ending at the current minute: 14.4M rows. The
  readings are rounded to sensor precision (speed 0.1 km/h, lat/lon 1e-6).
- **Ingest.** `WORKERS` (4) connections, each owning every 4th vehicle and streaming its readings
  in time order, like a live feed, as binary `COPY` statements of up to `BATCH` (100,000) rows.
  The data is loaded twice into a fresh `telemetry` hypertable (1-day chunks, segmentby
  `vehicle_id`, orderby `time DESC`, plus a `(vehicle_id, time DESC)` index). The first load goes
  straight into the columnstore (`SET timescaledb.enable_direct_compress_copy = on`), the second
  into the rowstore.
- **Queries** (each `RUNS` = 5 times, median, client-side wall time including fetching the rows):

  | query | on raw rows | on the continuous aggregate |
  |---|---|---|
  | `fleet-daily-km` | `max(odometer) - min(odometer)` per vehicle per day, joined to `vehicles` and `fleets`, summed per fleet | the same from hourly min/max odometer |
  | `region-speed-24h` | hourly average speed of moving vehicles per region, last 24 h (`avg(...) FILTER (WHERE speed_kmh > 0)` + 2 joins) | `sum / count` of the hourly moving-speed columns |
  | `overheating-by-model` | vehicles that went over 90 °C, per model, with the max temperature | vehicles whose hourly max was over 90 °C |
  | `fleet-lastpoint` | latest position and battery of each of fleet 7's 50 vehicles (`CROSS JOIN LATERAL ... ORDER BY time DESC LIMIT 1`) | - |
  | `vehicle-route-24h` | one vehicle's 2,880 readings of the last 24 h | - |

  They run on the rowstore, again after `convert_to_columnstore` on every chunk, and then on
  `telemetry_hourly`, a continuous aggregate with real-time aggregation (`materialized_only = false`).
  Finally the client inserts one reading and checks that it is already in the aggregate without
  a refresh.

## Run

```bash
make up      # TimescaleDB on localhost:5455 (user postgres, password tsdb-demo)
make run     # build the client (first time ~1 min), generate + ingest 14.4M rows twice, time the queries (~1.5 min)
make run VEHICLES=5000 DAYS=2 WORKERS=8   # bigger fleet, more connections
make status  # hypertables, chunk counts and sizes
make cli     # psql
make down    # remove containers, volume and the client image
```

`make run` also writes its output to `results/fleet-<UTC time>.txt` (gitignored). The client
container gets `CLIENT_CPUS=4`. The client image is built with `rust:1` and runs on
`debian:trixie-slim`.

## Results

2026-10-03, Docker Desktop 29.5.3 on an Apple M4 Pro (Docker VM: 11 CPUs, 24.4 GB, aarch64),
PostgreSQL 18.6 + TimescaleDB 2.30.2, server capped at 4 CPUs / 6 GB, client 4 CPUs, Docker VM
shared with other stacks. `make run` with the defaults:

```
1000 vehicles in 20 fleets, one reading every 30 s for 5 days = 14400000 rows; 4 COPY connections, up to 100000 rows per COPY

== ingest (binary COPY)
  straight into the columnstore:   14400000 rows in   2.5 s =   5773113 rows/s,   238.4 MiB on disk
  into the rowstore:               14400000 rows in  13.0 s =   1104461 rows/s,  2019.7 MiB on disk

== queries on the rowstore (6 chunks), median of 5 runs
== convert_to_columnstore: 5.7 s; 2019.7 MiB -> 212.6 MiB = 9.5x smaller
== continuous aggregate telemetry_hourly: 121000 rows, built in 1.9 s

query (median ms)        rows    rowstore  columnstore  cont. agg   what
--------------------------------------------------------------------------------------------------------------
fleet-daily-km            120      1299.2        630.6       40.8   km driven per fleet per day, all days
region-speed-24h          125       760.0        662.0       27.0   hourly avg speed of moving vehicles per region, last 24 h
overheating-by-model        4       153.8         84.4       28.6   vehicles over 90 C per model, all days
fleet-lastpoint            50         0.4          0.5          -   latest position + battery of each vehicle in fleet 7
vehicle-route-24h        2880         1.3          0.9          -   one vehicle's route (every reading), last 24 h

real-time aggregation: a reading inserted just now shows up in telemetry_hourly: true
```

## Why TimescaleDB wins here

- **Ingest:** binary `COPY` over 4 connections loads 1.1M rows/s into the rowstore and 5.8M rows/s
  straight into the columnstore. The columnstore path writes 238 MiB instead of 2 GiB and
  maintains no B-tree.
- **Storage:** the columnstore holds the 14.4M readings in 213 MiB instead of 2,020 MiB, 9.5x
  smaller. Per-vehicle segments of time-ordered, sensor-precision values compress well:
  delta-of-delta for timestamps, Gorilla-style XOR for floats.
- **Dashboards:** the continuous aggregate answers the fleet and region tiles in 27-41 ms,
  against 0.65-1.3 s on raw rows: 28-32x faster than the rowstore and 15-25x faster than the
  columnstore. Real-time aggregation keeps the newest, not yet materialized hour correct. A
  refresh policy (as in [`../single-node`](../single-node)) keeps it current.
- **The columnstore halves full scans** (`fleet-daily-km` 1.3 s -> 0.63 s,
  `overheating-by-model` 154 -> 84 ms). The 24-hour region query gains little (760 -> 662 ms)
  because it reads every column it groups and averages from the 1-2 newest chunks, and most of
  its time goes into the joins and the per-hour aggregation.
- **Point lookups stay Postgres-fast either way:** the last position of 50 vehicles takes 0.4 ms
  and one vehicle's day 1 ms, through the `(vehicle_id, time DESC)` index on the rowstore and the
  per-segment min/max metadata on the columnstore.
- **It is just Postgres.** Every query joins the hypertable or its aggregate with ordinary
  `vehicles`/`fleets` tables, using `FILTER` and `LATERAL`, with no separate metadata
  store. A specialised time-series database would need the metadata denormalized into
  tags.
