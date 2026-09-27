ARG GO_VERSION=1.25
#build stage
FROM golang:${GO_VERSION}-alpine AS builder
RUN apk add --no-cache git
WORKDIR /go/src/app
COPY . .
RUN go mod init
RUN go get -v ./...
RUN go install -v ./...

#final stage
FROM alpine:latest
RUN apk --no-cache add ca-certificates
COPY --from=builder /go/bin/app /multierror
CMD [ "./multierror" ]
