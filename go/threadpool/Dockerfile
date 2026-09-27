ARG GO_VERSION=1.25
#build stage
FROM golang:${GO_VERSION}-alpine AS builder
WORKDIR /go/src/app
COPY . .
RUN go test ./...
RUN go build -o /go/bin/threadpool .

#final stage
FROM alpine:latest
COPY --from=builder /go/bin/threadpool /threadpool
ENTRYPOINT [ "/threadpool" ]
CMD [ "-workers", "8", "-producers", "4", "-messages", "1000000" ]
