# YDB — single node

[`local-ydb`](https://ydb.tech/docs/en/reference/docker/start): storage and database
`/local` in one container, in-memory disks.

```bash
make up       # start and wait for the image's healthcheck
make test     # run sql/*.sql: create table, upsert, select, delete
make status   # cluster health from the viewer API
make cli      # interactive YQL shell
make down     # remove the container
```

- gRPC: `grpc://localhost:2136/local`
- Embedded UI: http://localhost:8765

The image is amd64-only and runs under emulation on Apple silicon.
