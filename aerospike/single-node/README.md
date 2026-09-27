# Aerospike Community Edition — single node

The stock `aerospike/aerospike-server` (CE) image with its built-in template config:
one node, namespace `test`, 1 GiB file storage, client port `localhost:3000`.

```bash
make up       # start and wait until the node is stable
make test     # run aql/test.aql (via aerospike-tools): insert, select, delete
make status   # asadm info
make cli      # interactive asadm
make down     # remove containers and the volume
```
