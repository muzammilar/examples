# ScyllaDB — single node

One ScyllaDB node (1 shard, 1 GiB, developer mode), CQL on `localhost:9042`.

```bash
make up       # start and wait for CQL to answer
make test     # run cql/test.cql: RF=1 keyspace, insert, select, delete
make status   # nodetool status
make cli      # interactive cqlsh
make down     # remove the container and its volume
```

A one-shot privileged `sysctl` container raises `fs.aio-max-nr` first; on Docker
Desktop that applies to the whole Docker VM until Docker restarts.
