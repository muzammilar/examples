# The feature-serving client: a static Go binary (go-sql-driver/mysql + net/http) on alpine.
FROM golang:1.26-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY main.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /feature-store .

FROM alpine:3.24
COPY --from=build /feature-store /usr/local/bin/feature-store
ENTRYPOINT ["feature-store"]
