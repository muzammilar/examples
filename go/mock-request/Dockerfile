ARG GO_VERSION=1.25
#build stage
FROM golang:${GO_VERSION}-alpine AS base
WORKDIR /httpmock
# download dependencies first for container cache and performance
COPY go.mod go.sum ./
RUN go mod download
COPY . .

# (re)generate the gomock mocks and run the unit tests
FROM base AS mockgen
RUN go generate ./...
CMD ["go", "test", "-v", "./..."]

# build static binaries: the CLI and the local demo target service
FROM base AS build
RUN CGO_ENABLED=0 go build -o /bin/httpmock ./cmd && \
    CGO_ENABLED=0 go build -o /bin/target ./cmd/target

# final image for the CLI / prober
FROM alpine:3.20 AS httpmock
RUN apk add --no-cache ca-certificates
COPY --from=build /bin/httpmock /bin/httpmock
ENTRYPOINT ["/bin/httpmock"]
CMD ["-url", "https://example.com"]

# a tiny HTTP service with mixed status codes and latencies to probe
FROM alpine:3.20 AS target
COPY --from=build /bin/target /bin/target
EXPOSE 8081
ENTRYPOINT ["/bin/target"]
