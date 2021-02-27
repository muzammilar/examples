#build stage
FROM golang:1.15-alpine AS builder
RUN apk add --no-cache git
WORKDIR /go/src/app
COPY . .
RUN go get -d -v ./...
RUN go install -v ./...
RUN ls -al /go/bin/

#final stage
FROM alpine:latest
RUN apk --no-cache add ca-certificates curl
COPY --from=builder /go/bin/app /app
ENTRYPOINT ./app
LABEL Name=chmtbulkingest Version=0.0.1
