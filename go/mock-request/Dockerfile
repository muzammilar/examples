ARG GO_VERSION=1.26
ARG ALPINE_VERSION=3.24
# must match the go.uber.org/mock version in go.mod (checked in the mockgen stage)
ARG MOCKGEN_VERSION=v0.6.0

#build stage
FROM golang:${GO_VERSION}-alpine AS base
WORKDIR /httpmock
# download dependencies first for container cache and performance
COPY go.mod go.sum ./
RUN go mod download
COPY . .

# (re)generate the gomock mocks and run the unit tests
FROM base AS mockgen
ARG MOCKGEN_VERSION
# pin mockgen to the go.mod version of go.uber.org/mock; installing it also
# caches the module, so the `go run ...mockgen@version` directive works offline
RUN test "$(go list -m -f '{{.Version}}' go.uber.org/mock)" = "${MOCKGEN_VERSION}" && \
    go install go.uber.org/mock/mockgen@${MOCKGEN_VERSION}
RUN go generate ./...
CMD ["go", "test", "-v", "./..."]

# build static binaries: the CLI and the local demo target service
FROM base AS build
RUN CGO_ENABLED=0 go build -o /bin/httpmock ./cmd && \
    CGO_ENABLED=0 go build -o /bin/target ./cmd/target

# final image for the CLI / prober
FROM alpine:${ALPINE_VERSION} AS httpmock
RUN apk add --no-cache ca-certificates
COPY --from=build /bin/httpmock /bin/httpmock
ENTRYPOINT ["/bin/httpmock"]
CMD ["-url", "https://example.com"]

# a tiny HTTP service with mixed status codes and latencies to probe
FROM alpine:${ALPINE_VERSION} AS target
COPY --from=build /bin/target /bin/target
EXPOSE 8081
ENTRYPOINT ["/bin/target"]
