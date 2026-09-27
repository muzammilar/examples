# Weaviate — single node

One Weaviate node with no vectorizer module (`vectorizer: none`): the client supplies
every vector. `make test` drives REST and GraphQL with curl + jq from a small `tools` container.

```bash
make up       # start and wait for /v1/.well-known/ready
make test     # run scripts/test.sh: collection, batch insert, nearVector, filtered nearVector, BM25, hybrid
make status   # node status and collections
make cli      # shell with curl + jq on the compose network (API at http://weaviate:8080)
make down     # remove containers and the volume
```

- REST: http://localhost:8080/v1 (e.g. `curl localhost:8080/v1/schema`)
- GraphQL: `POST http://localhost:8080/v1/graphql`
- gRPC: `localhost:50051`

No web UI ships with the server. Anonymous access is enabled, no API key. Request bodies
are in `requests/`; `make test` drops and recreates the `Landmark` collection each run.
`make status` uses curl and jq on the host.
