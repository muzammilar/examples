# NATS — single node

One `nats-server` with JetStream enabled (file storage in a volume), plus
[`nats-box`](https://github.com/nats-io/nats-box) as a `tools` profile service
for the `nats` CLI.

```bash
make up       # start and wait for /healthz (JetStream enabled)
make test     # run scripts/*.sh: pub/sub, request/reply, stream + pull consumer, KV bucket
make status   # nats server check jetstream
make cli      # interactive nats-box shell (NATS_URL already set)
make down     # remove containers and the volume
```

- Clients: `nats://localhost:4222` (no auth)
- HTTP monitoring: http://localhost:8222 (`/varz`, `/jsz`, `/healthz`)

Uses the `-alpine` image variant because the default `scratch` image has no
shell or `wget` for the healthcheck.
