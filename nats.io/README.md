# NATS.io

Website: https://nats.io/

- [`single-node/`](single-node) — one NATS server with JetStream on Docker Compose: pub/sub, request/reply, a stream with an explicit-ack consumer, and a KV bucket with history.
- [`docker-compose-cluster/`](docker-compose-cluster) — three routed servers with JetStream on Docker Compose: pub/sub across servers, an R3 stream and KV bucket, and stream-leader failover.

## Benchmark

`nats bench` on 4 CPUs / 6 GB (Apple M4 Pro, Docker VM aarch64, 2026-09-28): core pub/sub 2.3M msgs/s
(128 B, 1×1), request/reply p99 0.17 ms; a JetStream publish that waits for its ack does 15.6k/s,
~150× less than core NATS, and pipelining brings it back to ~320k/s. Full table and method:
[`single-node/README.md`](single-node/README.md#benchmark).
