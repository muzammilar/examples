# Aerospike Community Edition — single node

The stock `aerospike/aerospike-server` (CE) image with its built-in template config:
one node, namespace `test`, 1 GiB file storage, client port `localhost:3000`.

```bash
make up       # start and wait until the node is stable
make test     # truncate set test.users, create a numeric secondary index, then run
              # aql/test.aql (via aerospike-tools): typed bins, per-record TTL,
              # index equality/range queries, Lua record UDF + stream aggregation
make status   # asadm info
make cli      # interactive asadm
make down     # remove containers and the volume
```

Community Edition has no authentication (users/roles) and no TLS; anyone who reaches port 3000 has full access.

`aql/demo.lua` holds the two Lua UDFs; aql registers it from the tools container.
