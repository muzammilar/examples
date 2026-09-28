# YugabyteDB — single node

One YugabyteDB node started with `yugabyted start --background=false` (one yb-master and
one yb-tserver, replication factor 1), as in the
[Docker quick start](https://docs.yugabyte.com/stable/quick-start/docker/).

```bash
make up        # start and wait until YSQL and YCQL both answer
make test      # run ysql/*.sql (hash/range sharding, transaction, index, EXPLAIN DIST) and ycql/*.cql (TTL, JSONB, transactional table + index)
make status    # yugabyted status
make cli       # interactive ysqlsh
make cli-ycql  # interactive ycqlsh
make down      # remove the container (data lives in it)
```

- YSQL: `localhost:5433` (user `yugabyte`, db `yugabyte`, no password)
- YCQL: `localhost:9042`
- yugabyted UI: http://localhost:15433
- yb-master UI: http://localhost:7000, yb-tserver UI: http://localhost:9000

The `yugabytedb/yugabyte` image is multi-arch and runs natively on Apple silicon.
On macOS, AirPlay Receiver listens on port 7000; if `make up` fails to bind it, turn
AirPlay Receiver off or change the mapping to `7001:7000`.
