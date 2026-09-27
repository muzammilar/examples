ARG GO_VERSION=1.19
#build stage
FROM golang:${GO_VERSION}-alpine AS builder
RUN apk add --no-cache git
# for go vet (install gcc)
RUN apk add build-base
WORKDIR /go/src/app
COPY ./titan .
RUN apk add make git
RUN make mod-init
RUN make mod-deps
RUN make lint-deps
RUN make
RUN make install

#final stage
FROM alpine:latest
RUN apk --no-cache add ca-certificates
COPY --from=builder /go/bin/titan /titanserver
CMD ./titanserver
LABEL Name=titan Version=0.0.1
