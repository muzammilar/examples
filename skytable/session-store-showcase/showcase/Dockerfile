# Builds the showcase against the official `skytable` driver from crates.io. The driver pulls in
# native-tls (OpenSSL) even for plain TCP, hence libssl-dev here and libssl3 in the final image.
FROM rust:1.90-slim-bookworm AS build
RUN apt-get update && apt-get install -y --no-install-recommends pkg-config libssl-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /showcase
COPY Cargo.toml Cargo.lock ./
COPY src src
RUN cargo build --release --locked

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends libssl3 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /showcase/target/release/session-store-showcase /usr/local/bin/session-store-showcase
CMD ["session-store-showcase"]
