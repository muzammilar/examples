FROM golang:1.27.4-bookworm@sha256:ce59b41415e2524ae99926fe1f37ce48ba8c194dcbd2a56a65436cdcefa2bd34 AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY main.go ./
RUN go vet ./... && CGO_ENABLED=0 go build -trimpath -o /orders .
FROM scratch
COPY --from=build /orders /orders
USER 65532:65532
ENTRYPOINT ["/orders"]
