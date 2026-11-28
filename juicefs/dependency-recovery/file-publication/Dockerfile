FROM ubuntu:24.04@sha256:562111b88caa79c71391c0c4d3c2114a7d5c35915c20561fd893cb1e07929e6b AS build
RUN apt-get update && apt-get install -y --no-install-recommends g++ cmake ninja-build libssl-dev nlohmann-json3-dev \
    && rm -rf /var/lib/apt/lists/*
COPY posix-source/ /src/
RUN cmake -S /src -B /build -G Ninja -DCMAKE_BUILD_TYPE=Release -DPAYLOAD_SEED_PREFIX=96517 \
    && cmake --build /build
FROM ubuntu:24.04@sha256:562111b88caa79c71391c0c4d3c2114a7d5c35915c20561fd893cb1e07929e6b
RUN test "$(uname -m)" = aarch64 \
    && apt-get update && apt-get install -y --no-install-recommends libssl3t64 fuse3 util-linux python3 ca-certificates curl postgresql-client procps \
    && rm -rf /var/lib/apt/lists/*
ADD --checksum=sha256:19f72a30d3642a2f7971c8c4a31d2647de62f19520716e85c3a777de5f4c9036 https://github.com/juicedata/juicefs/releases/download/v1.4.2/juicefs-1.4.2-linux-arm64.tar.gz /tmp/juicefs.tar.gz
RUN tar -xzf /tmp/juicefs.tar.gz -C /usr/local/bin juicefs && rm /tmp/juicefs.tar.gz \
    && juicefs version | grep '1.4.2'
COPY --from=build /build/files-workload /usr/local/bin/files-workload
COPY mount.sh /usr/local/bin/mount-lab
ENTRYPOINT ["/bin/bash", "/usr/local/bin/mount-lab"]
