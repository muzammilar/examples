# The HTAP demo program (main.go, go-sql-driver/mysql); runtime image is a static binary on alpine.
FROM golang:1.26-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY main.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /htap-orders .

FROM alpine:3.24
COPY --from=build /htap-orders /usr/local/bin/htap-orders
ENTRYPOINT ["htap-orders"]
