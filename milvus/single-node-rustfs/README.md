# Milvus — single node (RustFS object storage)

Milvus standalone (all roles in one process) with its two dependencies, trimmed from
upstream's `milvus-standalone-docker-compose.yaml`: etcd for metadata, [RustFS](https://rustfs.com/)
for segment and index files. `make test` runs a short pymilvus script from a `tools` container.

Why RustFS: MinIO no longer publishes community images, so this variant swaps it for RustFS
(`rustfs/rustfs:1.0.0`, multi-arch, Apache-2.0), an S3-compatible store that Milvus talks to
through its usual `minio.*` settings. The sibling [`../single-node`](../single-node) keeps MinIO,
using Chainguard's MinIO image.

```bash
make up       # start etcd, RustFS and Milvus, wait for /healthz
make test     # run scripts/test.py: collection, insert, HNSW index + load, k-NN, expr filter, range search
make status   # container health and collections (REST)
make cli      # python REPL with a connected MilvusClient as `client`
make down     # remove containers and volumes
```

- gRPC + REST: `localhost:19530` (e.g. `curl -X POST localhost:19530/v2/vectordb/collections/list -d '{}'`)
- Web UI: http://localhost:9091/webui
- Health / metrics: http://localhost:9091/healthz, http://localhost:9091/metrics

RustFS (`rustfsadmin`/`rustfsadmin`, passed to Milvus as `MINIO_ACCESS_KEY_ID`/`MINIO_SECRET_ACCESS_KEY`)
and etcd are not published to the host; Milvus creates its bucket `a-bucket` on first start.
No auth on Milvus. Uses the same host ports as `../single-node`, so run one at a time.
The Milvus image is large (~1 GB) and needs ~1 minute to become healthy. A collection must be
indexed and loaded before it can be searched; `make test` drops and recreates `landmarks`.
