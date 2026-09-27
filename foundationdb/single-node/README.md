# FoundationDB — single node

One `fdbserver` process, `single` redundancy, `ssd-redwood-1` storage engine
(Redwood, FoundationDB's versioned B+tree engine, instead of the SQLite-based `ssd`).

```bash
make up       # start the container and `configure new single ssd-redwood-1`
make test     # run queries/*.fdbcli: engine check, set/get, range reads with limits,
              # clearrange, begin/commit and rollback transactions
make status   # fdbcli status
make cli      # interactive fdbcli
make down     # remove the container and its volume
```
