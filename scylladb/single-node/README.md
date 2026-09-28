# ScyllaDB — single node

One ScyllaDB node (1 shard, 1 GiB, developer mode), CQL on `127.0.0.1:9042`
(bound to localhost only: the image's default `AllowAllAuthenticator` means no authentication).

```bash
make up       # start and wait for CQL to answer
make test     # run cql/*.cql: consistency levels, lightweight transactions (IF ...),
              # TTL/WRITETIME, clustering range, secondary index, counters, collections, BATCH
make status   # nodetool status
make cli      # interactive cqlsh
make down     # remove the container and its volume
```

A one-shot privileged `sysctl` container raises `fs.aio-max-nr` first; on Docker
Desktop that applies to the whole Docker VM until Docker restarts.

`make test` prints two expected warnings from the demo keyspace:

- `Using Replication Factor replication_factor=1 lower than the minimum_replication_factor_warn_threshold=3
  is not recommended` — RF=1 keeps a single copy, fine for one node.
- `Creating an index in a keyspace that uses tablets requires the keyspace to remain RF-rack-valid ...` —
  keyspaces use tablets by default, and a secondary index then restricts later RF/rack changes.
