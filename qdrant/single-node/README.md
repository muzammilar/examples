# Qdrant — single node

One Qdrant node with default config, REST on `localhost:6333`, gRPC on `localhost:6334`.
`make test` drives the REST API with curl + jq from a small `tools` container.

```bash
make up       # start and wait for /readyz
make test     # run scripts/test.sh: collection, payload index, upsert, k-NN, filtered k-NN, recommend
make status   # version and collections
make cli      # shell with curl + jq on the compose network (API at http://qdrant:6333)
make down     # remove containers and the volume
```

- REST: http://localhost:6333 (e.g. `curl localhost:6333/collections`)
- Web UI: http://localhost:6333/dashboard
- gRPC: `localhost:6334`

No API key is set; anyone who can reach the port has full access. Request bodies are
in `requests/`; `make test` drops and recreates the `demo` collection each run. `make status`
uses curl and jq on the host.
