# Plain HTTP client (reqwest, no TLS), so the build needs no system libraries.
FROM rust:1.90-slim-bookworm AS build
WORKDIR /showcase
COPY Cargo.toml Cargo.lock ./
COPY src src
RUN cargo build --release --locked

FROM debian:bookworm-slim
COPY --from=build /showcase/target/release/iot-fleet-showcase /usr/local/bin/iot-fleet-showcase
CMD ["iot-fleet-showcase"]
