# SingleStore

Website: https://www.singlestore.com/

SingleStore (formerly MemSQL) is a distributed, MySQL wire-compatible SQL database: aggregators
plan queries and merge results, leaves store hash-sharded partitions in rowstore (in-memory)
or columnstore ("universal storage", the default) tables.

- [`single-node/`](single-node) — the official SingleStore Dev Image (a master aggregator + one leaf in one container) on Docker Compose; no license key needed.
- [`kubernetes-operator/`](kubernetes-operator) — 1 master aggregator + 1 child aggregator + 2 leaves on kind with the SingleStore Kubernetes Operator; needs a license key (`SINGLESTORE_LICENSE`).

SingleStore only publishes amd64 images; on Apple silicon both examples run under emulation.
