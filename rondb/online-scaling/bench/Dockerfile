# sysbench from Debian (no official sysbench image runs on arm64)
FROM debian:trixie-slim
ARG SYSBENCH_VERSION=1.0.20+ds-7+b1
RUN apt-get update \
	&& apt-get install -y --no-install-recommends sysbench=${SYSBENCH_VERSION} \
	&& rm -rf /var/lib/apt/lists/*
