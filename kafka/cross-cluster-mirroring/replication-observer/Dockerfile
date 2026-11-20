FROM rust:1.91-bookworm AS toolchain
RUN rustup component add rustfmt clippy

# Only used when intentionally refreshing the tracked lockfile.
FROM toolchain AS dependency-lock
LABEL lab.example.system="kafka-cross-cluster-mirroring" lab.example.role="lockfile-builder"
WORKDIR /src/consumer
COPY consumer/Cargo.toml ./
RUN mkdir src && echo 'fn main() {}' > src/main.rs \
    && cargo generate-lockfile \
    && cargo update -p rdkafka-sys --precise 4.10.0+2.12.1

FROM toolchain AS build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake libssl-dev libcurl4-openssl-dev pkg-config protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*
ENV CARGO_BUILD_JOBS=2
WORKDIR /src
COPY producer/proto producer/proto
COPY consumer consumer
WORKDIR /src/consumer
RUN cargo fmt --all --check \
    && cargo test --locked \
    && cargo clippy --locked --all-targets -- -D warnings \
    && cargo build --locked --release

FROM debian:bookworm-slim
LABEL lab.example.system="kafka-cross-cluster-mirroring" lab.example.role="consumer"
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates libssl3 zlib1g \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin app
COPY --from=build /src/consumer/target/release/order-consumer /usr/local/bin/order-consumer
USER 10001:10001
ENTRYPOINT ["/usr/local/bin/order-consumer"]
