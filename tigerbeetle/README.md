# TigerBeetle

Website: https://tigerbeetle.com/

Both examples run `client/demo.py` with the official Python client (accounts, transfers,
two-phase pending → post/void, a linked chain that fails atomically, a transfer rejected by
`debits_must_not_exceed_credits`) and use the built-in `tigerbeetle repl`.

- [`single-node/`](single-node) — one replica (`--replica-count=1`) on Docker Compose.
- [`docker-compose-cluster/`](docker-compose-cluster) — three replicas of one cluster, with a `make failover` that stops the primary.
