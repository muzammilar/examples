FROM ubuntu:24.04@sha256:562111b88caa79c71391c0c4d3c2114a7d5c35915c20561fd893cb1e07929e6b AS build
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates gpg g++ cmake ninja-build libssl-dev nlohmann-json3-dev
ADD --checksum=sha256:46207b03a3645e7d6f83790dc6c0fddb6bd08fd5a21843845cb1f294d96d8c3e https://download.ceph.com/keys/release.asc /tmp/ceph.asc
RUN gpg --dearmor < /tmp/ceph.asc > /usr/share/keyrings/ceph.gpg \
 && echo 'deb [signed-by=/usr/share/keyrings/ceph.gpg] https://download.ceph.com/debian-umbrella/ noble main' > /etc/apt/sources.list.d/ceph.list \
 && apt-get update && apt-get install -y --no-install-recommends libcephfs-dev=21.2.0-1noble
COPY files-workload/ /src/
RUN cmake -S /src -B /build -G Ninja -DCMAKE_BUILD_TYPE=Release && cmake --build /build
FROM scratch AS artifact
COPY --from=build /build/cephfs-files /cephfs-files
