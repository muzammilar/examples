FROM ubuntu:24.04@sha256:562111b88caa79c71391c0c4d3c2114a7d5c35915c20561fd893cb1e07929e6b AS build
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends g++ cmake ninja-build libssl-dev nlohmann-json3-dev
COPY .build/kernel-workload/ /src/
RUN cmake -S /src -B /build -G Ninja -DCMAKE_BUILD_TYPE=Release -DFILESYSTEM_MAGIC=0x00c36400 -DPAYLOAD_SEED_PREFIX=42193 -DSYNC_PUBLICATION_WITH_SYNCFS=ON && cmake --build /build
FROM scratch AS artifact
COPY --from=build /build/files-workload /files-workload
