# FoundationDB — single node

One `fdbserver` process, `single` redundancy, `ssd-redwood-1` storage engine
(Redwood, FoundationDB's versioned B+tree engine, instead of the SQLite-based `ssd`).

```bash
make up       # start the container, `configure new single ssd-redwood-1`, wait until healthy
make test     # run queries/*.fdbcli: engine check, set/get, range reads with limits,
              # clearrange, begin/commit and rollback transactions
make status   # fdbcli status
make cli      # interactive fdbcli
make down     # remove the container and its volume
```

No host port is published: FDB clients need a cluster file whose address they can reach, and the
server advertises its container address, so everything goes through `docker exec fdb fdbcli`.
`make up` polls `status json` until `.cluster.data.state.healthy` is `true` (up to 120 x 2 s)
with the `jq` that ships in the image.

`Memory availability - 8.0 GB per process` in `make status` is FDB's default per-process memory
limit (`--memory`, 8 GiB), not the memory actually free in the container or Docker VM.
