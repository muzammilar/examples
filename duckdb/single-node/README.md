# DuckDB — random data

DuckDB is in-process, so there is no server: `make up` pulls the official
`duckdb/duckdb` image (just the CLI binary) and runs a stdlib-only Python script
that writes seeded random data to `./data`: `events.csv` (1M rows), `users.json`
(10k NDJSON users with a tags list and a prefs object) and `countries.csv`. Every
target is a one-shot container with `./data` mounted.

```bash
make up       # pull images, generate ./data if missing (a few seconds)
make test     # run sql/*.sql: auto-detected CSV/NDJSON reads, SUMMARIZE, CSV x JSON joins,
              # GROUP BY ALL, QUALIFY top-N, running totals, UNNEST/structs/lambdas, PIVOT,
              # COPY to hive-partitioned Parquet and a pruned partition scan (EXPLAIN)
make status   # DuckDB version/platform, row counts and file sizes in ./data
make cli      # interactive duckdb shell on data/demo.duckdb (views events, users, countries)
make benchmark  # TPC-H SF1 (SMOKE=1: SF0.1) via the tpch extension, see below
make down     # remove containers and network, delete ./data
```

- The generator is seeded: `make down && make up` recreates byte-identical files.
  `python scripts/generate.py DIR N` changes the output dir and event count.
- `sql/01-read-files.sql` creates the views in `data/demo.duckdb` that the later
  files and `make cli` use; relative paths resolve against `/data` in the container.
- `.timer on` times are wall-clock inside the container. The CSV is re-parsed on
  every query; compare the same aggregate on CSV and Parquet in `04-parquet.sql`.
- DuckDB allows one writing process per database file: close `make cli` before
  running `make test`.
- Containers run as your host UID/GID so `./data` stays owned by you.
- `make down` leaves the pulled `duckdb/duckdb` and `python` images in place.

## Benchmark

`make benchmark` runs TPC-H with DuckDB's own
[`tpch` extension](https://duckdb.org/docs/stable/core_extensions/tpch) in the same
`duckdb/duckdb` image; [`bench/tpch.sh`](bench/tpch.sh) prints the CLI script
that is piped in. It does not need `make up` and does not touch `data/demo.duckdb`:

1. `INSTALL tpch; LOAD tpch;` then `CALL dbgen(sf = 1)` into a separate database file,
   `data/bench/tpch.duckdb` (`SMOKE=1` uses `sf = 0.1`; `make benchmark SF=10` for more).
   The extension is not built into the binary: the first run downloads it from
   `extensions.duckdb.org` (network needed) into `data/extensions`, reused until `make down`.
2. All 22 queries (`PRAGMA tpch(n)`), three rounds; median and best per query.
3. `EXPORT DATABASE ... (FORMAT parquet)`, then Q1, Q3, Q6, Q9, Q13 and Q18 again on views
   over the Parquet files, to compare DuckDB's own storage with Parquet.

Timings are DuckDB's own query latency from `PRAGMA enable_profiling = 'json'` (one
profile file per statement, read back with `read_json`), so result printing is not
included. A per-query table and a summary print at the end, and
`results/duckdb-<timestamp>.json` (git-ignored) gets the DuckDB version, scale factor,
threads, memory limit and the Docker VM's CPU count and memory. `data/bench` is deleted
afterwards.

What it shows: DuckDB is a vectorised, multi-threaded columnar engine, so full TPC-H at
SF1 (6M-row `lineitem`, multi-way joins, big aggregations) runs all 22 queries in well
under a second on a laptop. The same queries straight on the exported Parquet files run a few times slower
than on DuckDB's native storage but need no load step at all. This is an
in-process analytical engine, so compare it with other OLAP engines, not with the
lookup- and traversal-oriented databases in this repo.

### Sample results

TODO: fill in from a quiet machine (`make benchmark`, SF1).

| metric | DuckDB |
| --- | --- |
| dbgen SF1 (s) | TODO |
| 22 queries, sum of medians (s) | TODO |
| 22 queries, geometric mean (ms) | TODO |
| Parquet export (s) | TODO |
| Q1/Q3/Q6/Q9/Q13/Q18: native vs Parquet (s) | TODO |
