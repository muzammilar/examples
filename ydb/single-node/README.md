# YDB — single node

[`local-ydb`](https://ydb.tech/docs/en/reference/docker/start): storage and database
`/local` in one container, in-memory disks.

```bash
make up       # start and wait for the image's healthcheck
make test     # run sql/*.sql: row table with GLOBAL index (VIEW), TTL and Json column,
              # window aggregate, a multi-statement transaction, column-store analytics
make status   # cluster health from the viewer API
make cli      # interactive YQL shell
make down     # remove the container
```

- gRPC: `grpc://localhost:2136/local`
- Embedded UI: http://localhost:8765

local-ydb runs without authentication. It also serves gRPC over TLS (`grpcs://`, port
2135 inside the container, not published), but `make test` uses plain gRPC on 2136.

The image is amd64-only and runs under emulation on Apple silicon.
