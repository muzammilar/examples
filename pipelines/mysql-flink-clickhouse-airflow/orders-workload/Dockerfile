FROM golang:1.27.4-bookworm@sha256:ce59b41415e2524ae99926fe1f37ce48ba8c194dcbd2a56a65436cdcefa2bd34 AS build
WORKDIR /src
ENV GOMAXPROCS=1 GOFLAGS=-p=1
COPY go.mod go.sum ./
RUN go mod download
COPY *.go ./
RUN sha256sum *.go go.mod go.sum > /source.sha256 && go vet ./... && go test ./... && CGO_ENABLED=0 go build -trimpath -o /orders .
FROM scratch
COPY --from=build /orders /orders
COPY --from=build /source.sha256 /source.sha256
ENTRYPOINT ["/orders"]
