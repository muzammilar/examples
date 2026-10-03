# Plain HTTP client (reqwest, no TLS), so the build needs no system libraries.
FROM rust:1.90-slim-bookworm AS build
WORKDIR /app
COPY Cargo.toml Cargo.lock ./
COPY src src
RUN cargo build --release --locked

FROM debian:bookworm-slim
COPY --from=build /app/target/release/iot-fleet /usr/local/bin/iot-fleet
CMD ["iot-fleet"]
