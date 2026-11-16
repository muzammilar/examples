FROM golang:1.25.4-bookworm AS build
WORKDIR /src
COPY . .
RUN go vet ./... && CGO_ENABLED=0 go build -trimpath -o /wal-workload .
FROM debian:bookworm-slim
COPY --from=build /wal-workload /usr/local/bin/wal-workload
ENTRYPOINT ["wal-workload"]
