# CedarDB

Website: https://cedardb.com/

CedarDB is the commercial spin-off of [Umbra](https://umbra-db.com/), the research database from TUM, built by the same team and speaking the PostgreSQL wire protocol.

- [`single-node/`](single-node) — one CedarDB Community Edition server on Docker Compose, loaded with 3M generated rows for analytics, EXPLAIN plans, transactions, CSV and vectors.

CedarDB has no cluster or replication mode yet (high availability is an announced
Enterprise Edition feature), so there is no multi-node example.
