# go-ycsb (PingCAP's Go port of YCSB; the published image is amd64 only) built from source
# with just its TiKV driver: the upstream main registers ~20 database clients, some of which
# need cgo (RocksDB, FoundationDB, SQLite).
FROM golang:1.26-alpine AS build
ARG GO_YCSB_VERSION=v1.0.3
ARG GOPROXY=https://proxy.golang.org,direct
ENV GOPROXY=${GOPROXY} CGO_ENABLED=0 GOFLAGS=-mod=mod
WORKDIR /src
RUN dir=$(go mod download -json github.com/pingcap/go-ycsb@${GO_YCSB_VERSION} | sed -n 's/^\t"Dir": "\(.*\)",$/\1/p') \
	&& cp -r "$dir"/. . && chmod -R u+w . \
	&& sed -i '/go-ycsb\/db\//{/db\/tikv"/!d}' cmd/go-ycsb/main.go \
	&& go build -trimpath -ldflags="-s -w" -o /go-ycsb ./cmd/go-ycsb

FROM alpine:3.24
ARG GO_YCSB_VERSION=v1.0.3
ENV GO_YCSB_VERSION=${GO_YCSB_VERSION}
COPY --from=build /go-ycsb /usr/local/bin/go-ycsb
COPY --from=build /src/workloads /workloads
