FROM debian:bookworm-slim AS build
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl cmake g++ make pkg-config libprotobuf-dev protobuf-compiler \
    nlohmann-json3-dev libssl-dev libcurl4-openssl-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
ARG RDKAFKA_VERSION=2.12.1
# Official v2.12.1 release checksum; source commit e1db7eaa517f0a6438bc846a9c49ede73b9ea211.
ARG RDKAFKA_SHA256=ec103fa05cb0f251e375f6ea0b6112cfc9d0acd977dc5b69fdc54242ba38a16f
RUN curl --fail --location --retry 3 "https://github.com/confluentinc/librdkafka/archive/refs/tags/v${RDKAFKA_VERSION}.tar.gz" -o /tmp/rdkafka.tar.gz \
    && echo "${RDKAFKA_SHA256}  /tmp/rdkafka.tar.gz" | sha256sum --check \
    && mkdir /tmp/rdkafka && tar -xzf /tmp/rdkafka.tar.gz -C /tmp/rdkafka --strip-components=1 \
    && cmake -S /tmp/rdkafka -B /tmp/rdkafka/build -DCMAKE_BUILD_TYPE=Release \
       -DRDKAFKA_BUILD_EXAMPLES=OFF -DRDKAFKA_BUILD_TESTS=OFF \
       -DWITH_SSL=ON -DWITH_SASL=OFF -DWITH_CURL=OFF -DWITH_ZSTD=OFF -DENABLE_LZ4_EXT=OFF \
    && cmake --build /tmp/rdkafka/build --parallel 2 \
    && cmake --install /tmp/rdkafka/build
WORKDIR /src
COPY . .
RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build --parallel 2 \
    && ctest --test-dir build --output-on-failure

FROM debian:bookworm-slim
LABEL lab.example.system="kafka-strimzi-jbod" lab.example.role="producer"
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates libssl3 libprotobuf32 zlib1g \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin app
COPY --from=build /usr/local/lib/librdkafka*.so* /usr/local/lib/
RUN ldconfig
COPY --from=build /src/build/order-producer /usr/local/bin/order-producer
USER 10001:10001
ENTRYPOINT ["/usr/local/bin/order-producer"]
