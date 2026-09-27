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
