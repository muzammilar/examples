FROM rust:1.91.1-bookworm AS build
RUN apt-get update && apt-get install -y --no-install-recommends \
    cmake g++ libssl-dev pkg-config protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY proto/order.proto proto/order.proto
COPY consumer/Cargo.toml consumer/build.rs consumer/
COPY consumer/src/ consumer/src/
WORKDIR /src/consumer
RUN cargo build --release

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates libssl3 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin app
COPY --from=build /src/consumer/target/release/enriched-order-consumer /usr/local/bin/enriched-order-consumer
USER 10001:10001
ENTRYPOINT ["/usr/local/bin/enriched-order-consumer"]
