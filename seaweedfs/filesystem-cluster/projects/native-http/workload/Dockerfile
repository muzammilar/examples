FROM ubuntu:24.04@sha256:562111b88caa79c71391c0c4d3c2114a7d5c35915c20561fd893cb1e07929e6b AS build
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends g++ cmake ninja-build libssl-dev libcurl4-openssl-dev nlohmann-json3-dev
COPY projects/native-http/workload/ /src/
RUN cmake -S /src -B /build -G Ninja -DCMAKE_BUILD_TYPE=Release && cmake --build /build
FROM ubuntu:24.04@sha256:562111b88caa79c71391c0c4d3c2114a7d5c35915c20561fd893cb1e07929e6b
RUN apt-get update && apt-get install -y --no-install-recommends libcurl4t64 libssl3t64 ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=build /build/http-workload /usr/local/bin/http-workload
ENTRYPOINT ["sleep","infinity"]
