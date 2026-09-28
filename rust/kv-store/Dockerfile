# syntax=docker/dockerfile:1

FROM rust:1-slim-bookworm AS build
WORKDIR /src
COPY . .
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/src/target \
    cargo build --release --locked --bins \
    && cp target/release/kv-store target/release/kv /usr/local/bin/

FROM debian:bookworm-slim
RUN useradd --system --create-home --uid 10001 kv && mkdir /data && chown kv /data
COPY --from=build /usr/local/bin/kv-store /usr/local/bin/kv /usr/local/bin/
USER 10001
ENV KV_DATA_DIR=/data \
    KV_LISTEN=0.0.0.0:7000 \
    KV_CLUSTER=127.0.0.1:7000
EXPOSE 7000
# Healthy once the node is part of a cluster with a leader, so
# `docker compose up --wait` means the cluster is ready for writes.
HEALTHCHECK --interval=2s --timeout=3s --start-period=30s --retries=5 CMD ["kv", "health"]
ENTRYPOINT ["kv-store"]
