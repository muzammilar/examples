# Builds the showcase with the official questdb-rs client (crates.io) and tokio-postgres; the
# final image is debian:trixie-slim plus the binary, so no host Rust toolchain is needed.
FROM rust:1.98.1-trixie AS build
WORKDIR /showcase
COPY Cargo.toml Cargo.lock ./
COPY src src
RUN cargo build --release --locked

FROM debian:trixie-slim
COPY --from=build /showcase/target/release/market-data-showcase /usr/local/bin/market-data-showcase
CMD ["market-data-showcase"]
