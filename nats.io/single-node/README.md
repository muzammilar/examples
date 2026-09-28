# NATS — single node

One `nats-server` with JetStream enabled (file storage in a volume), plus
[`nats-box`](https://github.com/nats-io/nats-box) as a `tools` profile service
for the `nats` CLI.

```bash
make up         # start and wait for /healthz (JetStream enabled)
make test       # run scripts/*.sh: pub/sub, request/reply, stream + pull consumer, KV bucket
make app        # run app/*.py: the same concepts from Python (nats-py, asyncio)
make benchmark  # nats bench: core pub/sub, request/reply, JetStream publish + fetch (SMOKE=1: 1/10)
make status     # nats server check jetstream
make cli        # interactive nats-box shell (NATS_URL already set)
make down       # remove containers, the volumes and the built app image
```

- Clients: `nats://localhost:4222` (no auth)
- HTTP monitoring: http://localhost:8222 (`/varz`, `/jsz`, `/healthz`)

The Python examples in [`app/`](app/) use the official
[`nats-py`](https://github.com/nats-io/nats.py) client (pinned in `app/Dockerfile`):
`01_core.py` wildcard subjects and a two-worker queue group, `02_request_reply.py`
a responder service, `03_jetstream.py` acked publishes (seq), a durable pull
consumer and redelivery after `ack_wait`, `04_kv.py` put/get, watch and history.

Uses the `-alpine` image variant because the default `scratch` image has no
shell or `wget` for the healthcheck.

## Benchmark

`make benchmark` runs [`nats bench`](https://docs.nats.io/using-nats/nats-tools/nats_cli/natsbench)
from nats-box ([`bench/run.sh`](bench/run.sh)) and shows what persistence costs: core NATS
fans messages out from memory at millions per second, a JetStream publish waits for the server to
store the message in the stream and ack it.

| workload | what it measures |
|----------|------------------|
| `pubsub-1x1-128B`, `-1KiB` | 1 publisher -> 1 subscriber, `MSGS` messages (default 1,000,000) |
| `pubsub-4x4-128B`, `-1KiB` | `CLIENTS` (4) publishers share `MSGS`; each of 4 subscribers receives all of them |
| `request-reply` | one requester, one `nats bench service serve` responder: round-trip latency (`REQ_MSGS`, 20,000) |
| `js-pub-sync` | JetStream publish to stream `bench` (file storage, R1), one ack per message (`JS_SYNC_MSGS`, 20,000) |
| `js-pub-async` | the same with 500 publishes in flight (`JS_MSGS`, 500,000); latency is per batch of 500 |
| `js-fetch` | a durable pull consumer fetching those messages back, 500 per fetch, explicit acks |

```bash
make benchmark  # defaults above, ~1 min
make benchmark SMOKE=1            # a tenth of every message count
make benchmark MSGS=5000000 CLIENTS=8
```

It prints a summary table (publisher and all-subscribers msgs/s and MiB/s, p50/p99 latency for
request/reply and JetStream) and keeps the raw `nats bench` output plus parsed JSON with the
versions, parameters and Docker VM CPUs/memory in `results/nats-<UTC time>.{txt,json}`
(gitignored). The JSON is written by [`bench/report.py`](bench/report.py) (standard library,
run with `uv run --frozen` in `ghcr.io/astral-sh/uv:0.12.19-python3.13-trixie-slim`). The `bench`
stream is deleted afterwards. The client runs in the same Docker VM as the server, so both compete
for the same CPUs.

**Resource budget.** For the run [`bench/limits.sh`](bench/limits.sh) caps the `nats` container at
`BENCH_CPUS=4` / `BENCH_MEM=6g` (no swap) with `docker update` and restores it afterwards (Docker
cannot remove a memory limit from a running container, so "unlimited" goes back as the Docker VM's
total memory; `make down && make up` starts clean). The bench client has `cpus: 2` in compose
(`BENCH_CLIENT_CPUS`). The applied limits are in the JSON under `limits`. nats-server is a Go
program, and Go 1.25+ sizes `GOMAXPROCS` from the cgroup CPU limit and follows changes to it, so
the cap is honoured.

### Sample results

2026-09-28, `make benchmark` (defaults), Docker Desktop 29.5.3 on an Apple M4 Pro (Docker VM:
11 CPUs, 24.4 GB, aarch64, native images), NATS 2.15.0 capped at 4 CPUs / 6 GB, client 2 CPUs.

| workload | side | msgs/s | MiB/s | p50 ms | p99 ms |
|----------|------|-------:|------:|-------:|-------:|
| pubsub-1x1-128B | pub / sub | 2,319,576 / 2,321,019 | 283 | | |
| pubsub-1x1-1KiB | pub / sub | 1,287,140 / 1,247,431 | 1,229 | | |
| pubsub-4x4-128B | pub / sub | 1,018,386 / 4,070,949 | 124 / 497 | | |
| pubsub-4x4-1KiB | pub / sub | 767,831 / 3,013,418 | 750 / 2,970 | | |
| request-reply | request | 8,143 | | 0.121 | 0.173 |
| js-pub-sync | js pub sync | 15,642 | | 0.063 | 0.085 |
| js-pub-async | js pub async | 322,332 | | 1.276 (per 500) | 3.904 |
| js-fetch | js fetch | 339,677 | | 0.005 | 0.026 |

Core NATS fans out millions of messages per second from memory; a JetStream publish that waits
for its ack costs ~150x core throughput (15.6k/s). Pipelining 500 in flight gets back to ~320k/s.
Request/reply is bound by the round trip (~0.12 ms), not by the server.
