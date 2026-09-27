# ArangoDB — single node

One ArangoDB server (single-server mode), web UI and HTTP API on `localhost:8529`.
`make up` creates database `demo` with `people`, `cities`, a `knows` edge collection,
a persistent index and a named graph `social`.

```bash
make up       # start, wait for the healthcheck, create schema (aql/schema.js)
make test     # run aql/*.aql: inserts, edges, UPSERT, indexed filter, traversal, shortest path
make status   # server version and availability over HTTP
make cli      # interactive arangosh on database demo
make down     # remove the container and its volume
```

- HTTP API / web UI: http://localhost:8529 (user `root`, password `demo` — demo only)

Queries are idempotent (`overwriteMode: "replace"`), except `UPSERT`, which bumps
the visit counters on every run.

Since 3.12.5 there is one image for all editions; it reports `license: enterprise`
but runs under the ArangoDB Community License (free, 100 GiB dataset limit).
