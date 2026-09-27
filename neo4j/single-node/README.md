# Neo4j — single node

One Neo4j Community Edition server (512 MiB heap), Bolt on `localhost:7687` and
Neo4j Browser on http://localhost:7474.

```bash
make up       # start and wait for cypher-shell to answer
make test     # run cypher/*.cypher: constraint + index, MERGE, paths, shortestPath, aggregation
make status   # SHOW DATABASES
make cli      # interactive cypher-shell
make down     # remove the container and its volume
```

- Bolt: `bolt://localhost:7687`, HTTP/Browser: http://localhost:7474
- Credentials: `neo4j` / `demo-password` (fixed demo value, set via `NEO4J_AUTH`)

Community Edition has a single database (`neo4j`) and no clustering, RBAC or
node-key/existence constraints; uniqueness constraints and indexes work.
