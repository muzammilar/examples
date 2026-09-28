# go-tpc (PingCAP's TPC-C driver, no arm64 image published) built from source, plus
# sysbench from Debian (no official sysbench image runs on arm64)
FROM golang:1.26-alpine AS go-tpc
ARG GO_TPC_VERSION=v1.0.12
ARG GOPROXY=https://proxy.golang.org,direct
RUN CGO_ENABLED=0 GOPROXY=${GOPROXY} go install -trimpath -ldflags="-s -w" \
	github.com/pingcap/go-tpc/cmd/go-tpc@${GO_TPC_VERSION}

FROM debian:trixie-slim
ARG SYSBENCH_VERSION=1.0.20+ds-7+b1
ARG GO_TPC_VERSION=v1.0.12
ENV GO_TPC_VERSION=${GO_TPC_VERSION}
RUN apt-get update \
	&& apt-get install -y --no-install-recommends sysbench=${SYSBENCH_VERSION} \
	&& rm -rf /var/lib/apt/lists/*
COPY --from=go-tpc /go/bin/go-tpc /usr/local/bin/go-tpc
