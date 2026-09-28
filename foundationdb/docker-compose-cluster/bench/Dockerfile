# Benchmark client: the uv + Python 3.13 image (no Python download at run time) plus
# libfdb_c.so from the exact server image, so the client library matches the cluster.
ARG FDB_VERSION=7.3.79
FROM foundationdb/foundationdb:${FDB_VERSION} AS fdb
FROM ghcr.io/astral-sh/uv:0.12.19-python3.13-trixie-slim
COPY --from=fdb /usr/lib/libfdb_c.so /usr/lib/libfdb_c.so
