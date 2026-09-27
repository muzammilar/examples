ARG GO_VERSION=1.23
#build stage
FROM golang:${GO_VERSION}-alpine AS builder
WORKDIR /go/src/app
COPY go.mod ./
RUN go mod download
COPY . .
RUN go vet ./...
RUN CGO_ENABLED=0 go test ./...
RUN CGO_ENABLED=0 go build -o /go/bin/workerpool ./cmd/workerpool

#final stage
FROM alpine:latest AS pool
COPY --from=builder /go/bin/workerpool /workerpool
ENTRYPOINT ["/workerpool"]
CMD ["-duration", "20s"]
LABEL Name=dynamic-threadpool Version=0.0.1
