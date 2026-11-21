FROM rust:1.91-bookworm AS toolchain
RUN rustup component add rustfmt clippy

FROM toolchain AS build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake libssl-dev libcurl4-openssl-dev pkg-config protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*
ENV CARGO_BUILD_JOBS=2
WORKDIR /src
COPY proto proto
COPY consumer consumer
WORKDIR /src/consumer
RUN cargo fmt --all --check \
    && cargo test --locked \
    && cargo clippy --locked --all-targets -- -D warnings \
    && cargo build --locked --release

FROM debian:bookworm-slim
LABEL lab.example.system="kafka-connect-postgres-enrichment" lab.example.role="consumer"
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates libssl3 zlib1g \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin app
COPY --from=build /src/consumer/target/release/order-consumer /usr/local/bin/order-consumer
USER 10001:10001
ENTRYPOINT ["/usr/local/bin/order-consumer"]
