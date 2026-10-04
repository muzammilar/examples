ARG GO_VERSION=1.26
FROM golang:${GO_VERSION}-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY *.go *.lua ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /wallet .

FROM alpine:3.22
COPY --from=build /wallet /wallet
ENTRYPOINT ["/wallet"]
