ARG GO_VERSION=1.26
ARG ALPINE_VERSION=3.24

###############################################
# Builder stage
###############################################

# a single builder for all the binaries (admin, producer, consumer)
FROM golang:${GO_VERSION}-alpine AS builder
WORKDIR /go/src/app
# download the dependencies first (cached layer)
COPY ./trees/go.mod ./trees/go.sum ./
RUN go mod download
COPY ./trees .
# install the binaries
RUN CGO_ENABLED=0 go install -v ./admin ./producer ./consumer

###############################################
# Runner stage
###############################################

# admin (topic creation)
FROM alpine:${ALPINE_VERSION} AS admin
RUN apk --no-cache add ca-certificates
COPY --from=builder /go/bin/admin /treeadmin
CMD ["/treeadmin"]

# producer
FROM alpine:${ALPINE_VERSION} AS producer
RUN apk --no-cache add ca-certificates
COPY --from=builder /go/bin/producer /treeproducer
CMD ["/treeproducer"]

# consumer
FROM alpine:${ALPINE_VERSION} AS consumer
RUN apk --no-cache add ca-certificates
COPY --from=builder /go/bin/consumer /treeconsumer
CMD ["/treeconsumer"]
