FROM rust:1.91-bookworm AS build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake libssl-dev pkg-config protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*
RUN rustup component add rustfmt clippy
WORKDIR /src
COPY consumer/Cargo.toml consumer/Cargo.lock consumer/build.rs ./consumer/
COPY consumer/src ./consumer/src
COPY producer/proto ./producer/proto
WORKDIR /src/consumer
RUN cargo fmt --check \
    && cargo clippy --locked --all-targets -- -D warnings \
    && cargo test --locked \
    && cargo build --release --locked

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates libssl3 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin app
COPY --from=build /src/consumer/target/release/order-consumer /usr/local/bin/order-consumer
USER 10001:10001
ENTRYPOINT ["/usr/local/bin/order-consumer"]
