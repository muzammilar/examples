# build: the official Rust image; run: Debian slim with only the binary
FROM rust:1 AS build
WORKDIR /src
COPY Cargo.toml Cargo.lock ./
RUN mkdir src && echo 'fn main() {}' > src/main.rs && cargo build --release --locked && rm -rf src
COPY src ./src
RUN touch src/main.rs && cargo build --release --locked

FROM debian:trixie-slim
COPY --from=build /src/target/release/fleet /usr/local/bin/fleet
ENTRYPOINT ["fleet"]
