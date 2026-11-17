FROM rust:1.91.1-slim-bookworm AS rust
RUN rustup component add rustfmt clippy
FROM python:3.12.12-slim-bookworm
ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends clang cmake make g++ ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=rust /usr/local/cargo /usr/local/cargo
COPY --from=rust /usr/local/rustup /usr/local/rustup
ENV RUSTUP_HOME=/usr/local/rustup CARGO_HOME=/usr/local/cargo PATH=/usr/local/cargo/bin:$PATH
COPY rayforce.tar.gz /tmp/rayforce.tar.gz
RUN mkdir -p /opt/rayforce/lib && tar -xzf /tmp/rayforce.tar.gz --strip-components=1 -C /opt/rayforce && \
    case "$TARGETARCH" in arm64) march=armv8-a+simd ;; amd64) march=x86-64-v3 ;; *) exit 1 ;; esac && \
    make -C /opt/rayforce -j4 shared CC=gcc RAY_MARCH=$march GIT_HASH=38fd4be RAY_VERSION=3.0.2 && \
    mv /opt/rayforce/librayforce.so /opt/rayforce/lib/ && rm /tmp/rayforce.tar.gz
COPY duckdb-*.tar.gz /tmp/
RUN mkdir -p /opt/duckdb/lib /opt/duckdb/include && tar -xzf /tmp/duckdb-${TARGETARCH}.tar.gz -C /opt/duckdb && \
    mv /opt/duckdb/libduckdb.so /opt/duckdb/lib/ && mv /opt/duckdb/*.h /opt/duckdb/include/ && rm /tmp/duckdb-*.tar.gz
COPY analytics-workload /workload
RUN cmake -S /workload -B /build -DCMAKE_BUILD_TYPE=Release && cmake --build /build -j4 && \
    mkdir -p /opt/bench && cp /build/libbench*.so /build/cpp_* /opt/bench/
WORKDIR /workload/rust
RUN cargo fmt --check && \
    ENGINE=rayforce cargo build --locked --release && cp target/release/analytics-workload /opt/bench/rust_rayforce && \
    ENGINE=duckdb cargo build --locked --release && cp target/release/analytics-workload /opt/bench/rust_duckdb && \
    ENGINE=duckdb cargo clippy --locked --release -- -D warnings
ENV LD_LIBRARY_PATH=/opt/bench:/opt/rayforce/lib:/opt/duckdb/lib
WORKDIR /workload
ENTRYPOINT ["python", "/workload/python/main.py"]
