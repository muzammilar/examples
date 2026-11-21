FROM golang:1.25.4-bookworm AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY *.go ./
RUN test -z "$(gofmt -l *.go)" && go vet ./... && go test ./... && \
    CGO_ENABLED=0 go build -trimpath -o /orders-workload .

FROM scratch AS workload
LABEL org.examples.lab="cedardb-backup-recovery"
COPY --from=build /orders-workload /orders-workload
USER 10001
ENTRYPOINT ["/orders-workload"]

FROM postgres:17.6-bookworm@sha256:f3bd19c606e442c3d7bdfa8002e03fe260a1023351e0ea4598032022b68dd6e3 AS backup-tools
LABEL org.examples.lab="cedardb-backup-recovery"
COPY --from=build /orders-workload /orders-workload
ENTRYPOINT ["/orders-workload"]
