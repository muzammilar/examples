FROM golang:1.27.4-bookworm AS build
WORKDIR /src
COPY go.mod *.go ./
RUN go test ./... && go vet ./... && CGO_ENABLED=0 go build -trimpath -o /inventory-workload .
FROM debian:bookworm-slim
COPY --from=build /inventory-workload /usr/local/bin/inventory-workload
ENTRYPOINT ["inventory-workload"]
