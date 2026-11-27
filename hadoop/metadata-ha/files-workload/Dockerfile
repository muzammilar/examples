FROM examples/hadoop-metadata-ha:3.5.0 AS build
RUN apt-get update && apt-get install -y --no-install-recommends g++ cmake ninja-build libssl-dev nlohmann-json3-dev \
    && rm -rf /var/lib/apt/lists/*
COPY files-workload/ /work/
RUN cmake -G Ninja -S /work -B /work/build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /work/build --parallel 2
FROM examples/hadoop-metadata-ha:3.5.0
COPY --from=build /work/build/files-workload /opt/hadoop/bin/files-workload
USER hadoop
ENTRYPOINT ["bash", "/opt/hadoop/lab-scripts/workload-start.sh"]
