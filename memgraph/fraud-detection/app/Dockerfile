FROM golang:1.27.1-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY *.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags=-s -o /fraud .

FROM alpine:3.22
COPY --from=build /fraud /usr/local/bin/fraud
ENTRYPOINT ["fraud"]
