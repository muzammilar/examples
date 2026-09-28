# sysbench 1.0.20 from Debian trixie (multi-arch, so native on Apple silicon;
# linked against libmariadb, which speaks OceanBase's MySQL protocol).
FROM debian:trixie-20260918-slim
RUN apt-get update \
	&& apt-get install -y --no-install-recommends sysbench \
	&& rm -rf /var/lib/apt/lists/*
ENTRYPOINT ["sysbench"]
