# Milvus — standalone

Milvus standalone (all roles in one process) with its two dependencies, trimmed from
upstream's `milvus-standalone-docker-compose.yaml`: etcd for metadata, MinIO for segment and
index files. `make test` runs a short pymilvus script from a `tools` container.

```bash
make up       # start etcd, MinIO and Milvus, wait for /healthz
make test     # run scripts/test.py: collection, insert, HNSW index + load, k-NN, expr filter, range search
make status   # container health and collections (REST)
make cli      # python REPL with a connected MilvusClient as `client`
make down     # remove containers and volumes
```

- gRPC + REST: `localhost:19530` (e.g. `curl -X POST localhost:19530/v2/vectordb/collections/list -d '{}'`)
- Web UI: http://localhost:9091/webui
- Health / metrics: http://localhost:9091/healthz, http://localhost:9091/metrics

MinIO (`minioadmin`/`minioadmin`) and etcd are not published to the host. No auth on Milvus.
The Milvus image is large (~1 GB) and needs ~1 minute to become healthy. A collection must be
indexed and loaded before it can be searched; `make test` drops and recreates `landmarks`.
