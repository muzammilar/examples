FROM rust:1.91.1-bookworm@sha256:c1e5f19e773b7878c3f7a805dd00a495e747acbdc76fb2337a4ebf0418896b33 AS tools
RUN rustup component add rustfmt clippy
FROM tools AS build
ENV CARGO_BUILD_JOBS=2
WORKDIR /work
COPY catalog-workload/Cargo.toml catalog-workload/Cargo.lock ./
COPY catalog-workload/src ./src
RUN cargo fmt --check && cargo clippy --locked --all-targets -- -D warnings && cargo test --locked && cargo build --locked --release

FROM debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates gzip procps coreutils time && rm -rf /var/lib/apt/lists/*
COPY --from=build /work/target/release/embedded-catalog-lab /usr/local/bin/embedded-catalog-lab
COPY contract.json /opt/lab/contract.json
COPY catalog-workload/Cargo.lock /opt/lab/Cargo.lock
COPY catalog-workload/src /opt/lab/src
ENTRYPOINT ["embedded-catalog-lab"]
CMD ["verify"]
