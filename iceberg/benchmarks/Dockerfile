FROM golang:1.26-bookworm

WORKDIR /bench
COPY go.mod ./
COPY go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=1 go build -trimpath -ldflags="-s -w" -o /usr/local/bin/iceberg-benchmark ./cmd/benchmark

ENTRYPOINT ["/usr/local/bin/iceberg-benchmark"]
