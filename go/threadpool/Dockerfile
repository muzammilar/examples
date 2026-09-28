ARG GO_VERSION=1.26
#build stage
FROM golang:${GO_VERSION}-alpine AS builder
WORKDIR /go/src/app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN go test ./...
RUN go build -o /go/bin/threadpool .

#final stage
FROM alpine:3.24
COPY --from=builder /go/bin/threadpool /threadpool
# Metrics port (only used when -metrics-addr is set).
EXPOSE 8080
ENTRYPOINT [ "/threadpool" ]
CMD [ "-workers", "8", "-producers", "4", "-messages", "1000000" ]
