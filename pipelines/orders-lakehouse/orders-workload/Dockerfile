FROM golang:1.27.4-bookworm@sha256:ce59b41415e2524ae99926fe1f37ce48ba8c194dcbd2a56a65436cdcefa2bd34 AS build
WORKDIR /src
ENV GOMAXPROCS=1 GOFLAGS=-p=1 GOTOOLCHAIN=local
COPY orders-workload/go.mod orders-workload/go.sum ./
RUN go mod download
COPY orders-workload/ ./
RUN go vet ./... && go test ./... \
    && CGO_ENABLED=0 go build -trimpath -o /producer ./cmd/producer \
    && CGO_ENABLED=0 go build -trimpath -o /lake-control ./cmd/lake-control
FROM scratch
COPY --from=build /producer /lake-control /
COPY deploy/catalog.json /app/catalog.json
ENTRYPOINT ["/producer"]
