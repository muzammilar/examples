# Builds the app against the official `skytable` driver from crates.io. The driver pulls in
# native-tls (OpenSSL) even for plain TCP, hence libssl-dev here and libssl3 in the final image.
FROM rust:1.90-slim-bookworm AS build
RUN apt-get update && apt-get install -y --no-install-recommends pkg-config libssl-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY Cargo.toml Cargo.lock ./
COPY src src
RUN cargo build --release --locked

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends libssl3 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /app/target/release/session-store /usr/local/bin/session-store
CMD ["session-store"]
