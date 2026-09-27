ARG GO_VERSION=1.19

###############################################
# Builder stage
###############################################

# producer
FROM golang:${GO_VERSION}-alpine AS producerbuilder
RUN apk add --no-cache git
WORKDIR /go/src/app
COPY ./trees .
# add missing dependencies
RUN go mod tidy
# install the module
RUN go install -v ./producer

ARG GO_VERSION

# consumer
FROM golang:${GO_VERSION}-alpine AS consumerbuilder
RUN apk add --no-cache git
WORKDIR /go/src/app
COPY ./trees .
# add missing dependencies
RUN go mod tidy
# install the module
RUN go install -v ./consumer

###############################################
# Runner stage
###############################################

#producer
FROM alpine:latest AS producer
RUN apk --no-cache add ca-certificates
COPY --from=producerbuilder /go/bin/producer /treeproducer
CMD ./treeproducer

#consumer
FROM alpine:latest AS consumer
RUN apk --no-cache add ca-certificates
COPY --from=consumerbuilder /go/bin/consumer /treeconsumer
CMD ./treeconsumer
