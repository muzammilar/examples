# FoundationDB — single node

One `fdbserver` process, `single` redundancy, `ssd` storage engine.

```bash
make up       # start the container and `configure new single ssd`
make test     # run queries/test.fdbcli: set, get, getrange, clear
make status   # fdbcli status
make cli      # interactive fdbcli
make down     # remove the container and its volume
```
