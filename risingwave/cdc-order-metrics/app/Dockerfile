# The order-metrics program (main.go, jackc/pgx); runtime image is a static binary on alpine.
FROM golang:1.26-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY main.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /cdc-order-metrics .

FROM alpine:3.24
COPY --from=build /cdc-order-metrics /usr/local/bin/cdc-order-metrics
ENTRYPOINT ["cdc-order-metrics"]
