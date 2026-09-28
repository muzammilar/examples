# TigerBeetle — 3-replica cluster with Docker Compose

Three replicas of cluster `0`, as in the [Docker Compose recipe](https://docs.tigerbeetle.com/operating/deploying/docker/#run-a-multi-node-cluster-using-docker-compose):
each formats its own data file (`--replica=N --replica-count=3`) on first start, then runs
`tigerbeetle start --addresses=<all three replicas>`. Three replicas replicate every commit to a
quorum of 2 and survive one failed replica; the docs recommend
[6 replicas on separate machines](https://docs.tigerbeetle.com/operating/cluster/) for production.

```bash
make up        # start the replicas (they format on first start and elect a primary)
make test      # client/demo.py via the Python client against all three addresses, then the REPL
make failover  # stop the primary: view change, transfer 501 commits on the other two; restart it,
               # stop another replica, transfer 502 commits via the old primary; restart everything
make status    # container state, current primary, each replica's last view/role
make cli       # interactive tigerbeetle repl
make down      # remove containers and the three data volumes
```

- Replica `i` is `tigerbeetle-i` at `10.203.53.1i:3000` on the `tigerbeetle` network; host ports
  `3033`, `3034`, `3035` (override with `TIGERBEETLE_PORT_0..2`). A host client passes
  `127.0.0.1:3033,127.0.0.1:3034,127.0.0.1:3035` — the order must match the replica indexes.
- `--addresses` takes IPs only, identical and in replica order on every replica and client. The
  docs use `network_mode: host`; this example gives each replica a static IP on a bridge network
  (`10.203.53.0/24`) instead, which also works on Docker Desktop.
- The primary of view `v` is replica `v mod 3`; `make status` derives it from the replicas'
  `transition_to_normal … view=` log lines.
- Image `ghcr.io/tigerbeetle/tigerbeetle:0.17.9` (override with `TIGERBEETLE_VERSION`); test client
  `python:3.13-slim` + `pip install tigerbeetle==0.17.9`. Server and client need io_uring
  (`security_opt: seccomp=unconfined`); `cap_add: IPC_LOCK` allows memory locking.
- `--cache-grid=256MiB` per replica; each replica still allocates ~2.3 GiB (~7 GiB in total).
