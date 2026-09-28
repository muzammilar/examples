# TiKV — PD + 3 TiKV with Docker Compose, no TiDB

TiKV used directly as a distributed key-value store: 3 PD (placement driver: metadata,
TSO timestamps, region scheduling) and 3 TiKV stores from the multi-arch `pingcap/pd` and
`pingcap/tikv` images, pinned to `v8.5.8`. The demo client in `client/` is a small Go
program on [`tikv/client-go`](https://github.com/tikv/client-go) (the client TiDB itself
uses, at the version TiDB v8.5.8 pins), built in a `golang` container. `make up` waits
until all three stores are Up and every region has its three replicas.

```bash
make up        # start PD and TiKV, build the client, wait for 3 stores Up and full replication
make test      # run the client: RawKV put/get/scan/delete, TxnKV commit, optimistic and pessimistic conflicts
make status    # pd-ctl member, store, region and replication config
make cli       # interactive pd-ctl
make down      # remove containers and volumes
```

- PD API: http://localhost:22379/pd/api/v1/stores (`PD_PORT` overrides); clients
  outside Docker can't reach the stores (they advertise `tikvN:20160`), so run them
  on the compose network like `make test` does
- other versions: `TIKV_VERSION=v8.5.7 make up`

The client (`client/main.go`):
- **RawKV**: `Put`, `BatchPut`, `Get`, `Scan` over a prefix, `Delete`, `DeleteRange`.
- **TxnKV**: a transaction writing three keys atomically; two optimistic transactions
  writing the same key (the second commit fails with a write conflict); a pessimistic
  transaction holding a lock while another one's no-wait `LockKeys` fails, then succeeds
  after the first commits.

Raw and transactional keys use separate prefixes (`raw/`, `txn/`): with API V1 (the
default) the two APIs must not share keys.

tikv.org's [TiKV in 5 minutes](https://tikv.org/docs/latest/concepts/tikv-in-5-minutes/)
starts a local cluster with `tiup playground --mode tikv-slim` and uses the Python
`tikv-client`. This example uses Docker Compose with pinned images instead, so only
Docker is needed, and Go instead of Python: the `tikv-client` wheels on PyPI are x86-64
only for Linux (building it on arm64 needs a Rust toolchain).

Notes:
- `config/tikv.toml` shrinks TiKV for a laptop: 256 MB block cache per store, no 5 GB
  reserved disk and a 2 GB reported capacity (on a Docker disk more than 80% full, PD
  would otherwise treat every store as low-space and not place replicas).
- A new cluster has five regions (split at the `r` and `x` API V2 keyspace prefixes) with
  all leaders on the first store; PD's balance-leader scheduler spreads them over a few minutes.
