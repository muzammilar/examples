ARG QDRANT_IMAGE=qdrant/qdrant:v1.18.3
FROM golang:1.25.4-bookworm AS build
WORKDIR /src
COPY go.mod *.go ./
RUN go vet -mod=readonly ./... && CGO_ENABLED=0 go build -mod=readonly -trimpath -o /workload .
FROM debian:bookworm-slim AS workload
COPY --from=build /workload /usr/local/bin/workload
ENTRYPOINT ["workload"]
FROM ${QDRANT_IMAGE} AS server
COPY --from=build /workload /probe
