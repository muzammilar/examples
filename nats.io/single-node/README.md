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

### Sample results

TODO: numbers from a quiet machine.

| workload | side | msgs/s | MiB/s | p50 ms | p99 ms |
|----------|------|-------:|------:|-------:|-------:|
| pubsub-1x1-128B | pub / sub | TODO | TODO | | |
| pubsub-1x1-1KiB | pub / sub | TODO | TODO | | |
| pubsub-4x4-128B | pub / sub | TODO | TODO | | |
| pubsub-4x4-1KiB | pub / sub | TODO | TODO | | |
| request-reply | request | TODO | | TODO | TODO |
| js-pub-sync | js pub sync | TODO | | TODO | TODO |
| js-pub-async | js pub async | TODO | | TODO | TODO |
| js-fetch | js fetch | TODO | | TODO | TODO |
