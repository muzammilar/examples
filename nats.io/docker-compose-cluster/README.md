# NATS — 3-server cluster with JetStream

Three `nats-server` nodes (`nats-1..3`) routed into one cluster `demo`, JetStream enabled on
all of them (file storage, one volume each), plus [`nats-box`](https://github.com/nats-io/nats-box)
as a `tools` profile service for the `nats` CLI. All three share [`nats.conf`](nats.conf);
only `server_name` differs, set from each container's `SERVER_NAME`.

```bash
make up        # start, wait for /healthz on all three, list the servers
make test      # run scripts/*.sh: membership + meta leader, pub/sub across servers, R3 stream + consumer, R3 KV
make failover  # stop the leader of an R3 stream: new leader, publish + consume go on; restart, catch up
make status    # nats server report jetstream + nats stream report
make cli       # interactive nats-box shell (NATS_URL lists all three servers)
make down      # remove containers and volumes
```

- Clients: `nats://127.0.0.1:4223`, `:4224`, `:4225` (nats-1, -2, -3)
- HTTP monitoring: http://127.0.0.1:8223, `:8224`, `:8225` (`/varz`, `/routez`, `/jsz`, `/healthz`)
- Ports are bound to localhost only and offset from [`../single-node`](../single-node) (4222/8222).

Accounts: clients without credentials land in account `APP` (`no_auth_user`), where JetStream
is enabled. `SYS` is the system account; `nats server ...` commands need its user
(`--user sys --password sys`). Demo passwords, localhost only.

What `make test` checks ([`scripts/`](scripts), each assertion fails the run):

1. `01-cluster.sh`: `nats server list` shows nats-1..3 in cluster `demo` with JetStream, and
   every server reports the same meta leader (`nats server report jetstream` prints the RAFT meta group).
2. `02-core.sh`: a subscriber on nats-1 receives what a publisher on nats-3 sends (the route carries it).
3. `03-stream.sh`: stream `ORDERS` with `--replicas 3`: 3 messages, a leader plus two current
   followers on the three servers; an R3 pull consumer with explicit acks acks all 3.
4. `04-kv.sh`: KV bucket `config` with `--replicas 3` (the stream `KV_config`: leader + 2 followers).

The scripts drop and recreate their stream / bucket, so `make test` can run again.

`make failover` ([`failover/`](failover)) creates stream `EVENTS` (R3) with an R3 consumer, publishes
and consumes 5 messages, then stops the stream's current leader. Within a bounded wait (30 s) the two
remaining replicas elect a new leader; 5 more messages are published with `--jetstream` (each waits
for the ack, i.e. a quorum of 2 of 3 stored it) and consumed, and `nats stream info` lists the
stopped server as an offline, non-current replica. The server is started again, and within 60 s its
replica is current and its own `/jsz` shows all 10 messages.

A 3-node JetStream cluster survives one server down; with two down there is no quorum for the
meta group or any R3 stream, and JetStream API calls and R3 publishes fail until one comes back.
