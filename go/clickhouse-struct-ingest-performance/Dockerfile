ARG GO_VERSION=1.19
#build stage
FROM golang:${GO_VERSION}-alpine AS builder
RUN apk add --no-cache git
WORKDIR /go/src/app
COPY . .
RUN go get -v ./...
RUN go install -v ./...
RUN ls -al /go/bin/

#final stage
FROM alpine:latest
RUN apk --no-cache add ca-certificates curl
COPY --from=builder /go/bin/app /app
ENTRYPOINT ./app
LABEL Name=chstructingest Version=0.0.1
