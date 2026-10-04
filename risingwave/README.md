# RisingWave

Website: https://risingwave.com/ · GitHub: https://github.com/risingwavelabs/risingwave

Streaming database (Postgres wire protocol): sources and tables feed materialized views that are
maintained incrementally; state lives in Hummock, an LSM tree on object storage. RisingWave 3.1.0.

| Folder | What |
|---|---|
| [`single-node/`](single-node) | `single_node` mode (all components in one process, local filesystem state store) on Docker Compose; psql walkthrough of tables, a datagen source, join/aggregate MVs, `EMIT ON WINDOW CLOSE`, sink into table and a native Postgres sink |

## Benchmark summary

Apple M4 Pro, Docker VM aarch64. Full numbers in each example.

| Example | Date | Setup | Result |
|---|---|---|---|
| [single-node](single-node/README.md#results) | 2026-10-04 | one container, 4 CPUs / 8 GB | healthy 5.3 s after `make up`; walkthrough (10 streaming jobs) 14.4 s; 318 MiB resident |

## Known issues

RisingWave 3.1.0, 2026-10-04.

- **Image size**: `risingwavelabs/risingwave:v3.1.0` is 2.52 GB compressed and 11.2 GB unpacked
  in the Docker VM.
- **Free license limit**: the Community Edition loads a default license
  (`rw-default-all-4-core`, `rwu_limit: Some(4)`) that enables paid features for clusters up to
  4 RWU (4 CPU cores, 16 GiB). Streaming parallelism is shown as `bounded(4)`.
  [Premium edition](https://docs.risingwave.com/get-started/rw-premium-edition-intro).
- `single_node` panics at a 6 GB memory limit (compactor memory assertion); 8 GB works
  ([details](single-node/README.md#known-issues)).
- `EMIT ON WINDOW CLOSE` and `APPEND ONLY` tables are marked experimental.
