# Builds the official TigerBeetle Rust client from source at the server's tag (it is not on
# crates.io yet): `zig build clients:rust` makes the static tb_client library the crate links.
ARG TIGERBEETLE_VERSION=0.17.9

FROM debian:bookworm-slim AS tb-client
ARG TIGERBEETLE_VERSION
# the oldest client release the server accepts (`tigerbeetle version --verbose` on the image)
ARG TIGERBEETLE_CLIENT_MIN=0.16.4
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl git xz-utils \
    && rm -rf /var/lib/apt/lists/*
# the build script lists past release tags (git history), but needs no old file contents
RUN git clone --quiet --filter=blob:none --branch "${TIGERBEETLE_VERSION}" \
    https://github.com/tigerbeetle/tigerbeetle.git /tigerbeetle
WORKDIR /tigerbeetle
# stamp the client with the release it was built from, so the server's version check accepts it
RUN ./zig/download.sh && ./zig/zig build clients:rust -Drelease \
    -Dconfig-release="${TIGERBEETLE_VERSION}" -Dconfig-release-client-min="${TIGERBEETLE_CLIENT_MIN}"

FROM rust:1.90-slim-bookworm AS build
COPY --from=tb-client /tigerbeetle/src/clients/rust /tigerbeetle/src/clients/rust
WORKDIR /app
COPY Cargo.toml Cargo.lock ./
COPY src src
RUN cargo build --release --locked

FROM debian:bookworm-slim
COPY --from=build /app/target/release/payments-ledger /usr/local/bin/payments-ledger
CMD ["payments-ledger"]
