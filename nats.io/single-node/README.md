# NATS — single node

One `nats-server` with JetStream enabled (file storage in a volume), plus
[`nats-box`](https://github.com/nats-io/nats-box) as a `tools` profile service
for the `nats` CLI.

```bash
make up       # start and wait for /healthz (JetStream enabled)
make test     # run scripts/*.sh: pub/sub, request/reply, stream + pull consumer, KV bucket
make app      # run app/*.py: the same concepts from Python (nats-py, asyncio)
make status   # nats server check jetstream
make cli      # interactive nats-box shell (NATS_URL already set)
make down     # remove containers, the volume and the built app image
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
