FROM spark:4.3.0-scala2.13-java21-python3-ubuntu@sha256:e1831ac4e26f91c80de8c360d7853ff507b08934ed0974a2823ec7080f73f3c7 AS distribution
FROM eclipse-temurin:17-jre-noble@sha256:ad806840ae514b83d3d8e3a00a5c1b7302fb0a6ae02bbbaf8a8bf1ffdf5367c6
RUN apt-get update && apt-get install -y --no-install-recommends python3 procps && rm -rf /var/lib/apt/lists/*
COPY --from=distribution /opt/spark /opt/spark
COPY replay.py /app/replay.py
ENV SPARK_HOME=/opt/spark PATH=/opt/spark/bin:$PATH PYSPARK_PYTHON=/usr/bin/python3 AWS_EC2_METADATA_DISABLED=true
ENTRYPOINT ["/opt/spark/bin/spark-submit", "--master", "local[1]", "--driver-memory", "512m", "--conf", "spark.ui.enabled=false", "--conf", "spark.sql.shuffle.partitions=2", "--conf", "spark.sql.adaptive.enabled=false", "/app/replay.py"]
