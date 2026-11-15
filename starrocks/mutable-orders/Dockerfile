FROM golang:1.25.4-bookworm AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY *.go ./
RUN go vet -mod=readonly ./... \
 && CGO_ENABLED=0 go build -mod=readonly -trimpath -o /client .
FROM debian:bookworm-slim
COPY --from=build /client /usr/local/bin/client
ENTRYPOINT ["client"]
