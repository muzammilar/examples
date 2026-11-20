FROM golang:1.27.4-bookworm AS toolchain
RUN apt-get update && apt-get install -y --no-install-recommends protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*
RUN go install google.golang.org/protobuf/cmd/protoc-gen-go@v1.36.12
FROM toolchain AS build
ENV GOMAXPROCS=2
WORKDIR /src
COPY proto/order.proto /src/proto/order.proto
COPY processor/go.mod processor/go.sum /src/processor/
WORKDIR /src/processor
RUN go mod download
COPY processor/*.go ./
RUN PATH="$(go env GOPATH)/bin:$PATH" protoc --go_out=. --go_opt=module=example.com/kafka-enricher -I /src/proto /src/proto/order.proto \
    && test -z "$(gofmt -l .)" \
    && go vet ./... \
    && go test -race ./... \
    && CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /out/enricher .

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/enricher /enricher
EXPOSE 8080
ENTRYPOINT ["/enricher"]
