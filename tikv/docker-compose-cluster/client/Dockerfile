# Build the demo client with client-go (the Go client TiDB itself uses); the
# runtime image is a static binary on alpine.
FROM golang:1.26-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY main.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /tikv-demo .

FROM alpine:3.24
COPY --from=build /tikv-demo /usr/local/bin/tikv-demo
ENTRYPOINT ["tikv-demo"]
