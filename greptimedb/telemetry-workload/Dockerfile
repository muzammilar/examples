ARG BASE_IMAGE=victoriametrics/victoria-logs:v1.54.0
FROM golang:1.25.4-bookworm AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -mod=readonly -trimpath -o /client .
FROM debian:bookworm-slim AS client
COPY --from=build /client /usr/local/bin/client
ENTRYPOINT ["client"]

FROM ${BASE_IMAGE} AS service
COPY --from=build /client /probe
