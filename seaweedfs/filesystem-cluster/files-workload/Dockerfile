FROM ubuntu:24.04@sha256:562111b88caa79c71391c0c4d3c2114a7d5c35915c20561fd893cb1e07929e6b AS build
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends g++ cmake ninja-build libssl-dev nlohmann-json3-dev
COPY files-workload/ /src/
RUN cmake -S /src -B /build -G Ninja -DCMAKE_BUILD_TYPE=Release && cmake --build /build
FROM chrislusf/seaweedfs:4.55@sha256:7ca46aeae2f223b37e5d91593c17ab37738eec6df6ab69b48a0487f7643342b9 AS seaweed
FROM ubuntu:24.04@sha256:562111b88caa79c71391c0c4d3c2114a7d5c35915c20561fd893cb1e07929e6b
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends libssl3t64 fuse3 util-linux python3 iproute2 ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=seaweed /usr/bin/weed /usr/bin/weed
COPY --from=build /build/files-workload /usr/local/bin/files-workload
COPY files-workload/mount.sh /usr/local/bin/mount-lab
COPY scripts/native_audit.py /opt/lab/native_audit.py
ENTRYPOINT ["/bin/bash", "/usr/local/bin/mount-lab"]
