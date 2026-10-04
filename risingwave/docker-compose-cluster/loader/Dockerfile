# Failover load generator (main.go, jackc/pgx); runtime image is a static binary on alpine.
FROM golang:1.26-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY main.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /loader .

FROM alpine:3.24
COPY --from=build /loader /usr/local/bin/loader
ENTRYPOINT ["loader"]
