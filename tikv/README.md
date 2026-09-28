# TiKV

Website: https://tikv.org/

- [`docker-compose-cluster/`](docker-compose-cluster) — 3 PD and 3 TiKV (no TiDB) on Docker Compose, with a Go `client-go` demo of RawKV and TxnKV (optimistic and pessimistic conflicts) and `pd-ctl` status.

## Benchmark

go-ycsb against 3 PD + 3 TiKV, 6 CPUs / 14 GB (memory raised from the usual 12 GB, because TiKV needs >2.8 GB per store; Apple M4 Pro, Docker VM aarch64, 2026-09-28): workload C reads 27.4k ops/s RawKV and 20.5k TxnKV; workload A updates ~5k/s at p50 2.0 ms raw vs 2.5 ms txn. A PD timestamp doubles read latency (0.18 → 0.37 ms p50), but a Percolator update costs only ~25% more than a raw Raft write. Full table and method: [`docker-compose-cluster/README.md`](docker-compose-cluster/README.md#benchmark).
