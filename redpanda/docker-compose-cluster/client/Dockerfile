# Load generator + loss check (main.go, franz-go); static binary on alpine.
FROM golang:1.26-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY main.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /load .

FROM alpine:3.24
COPY --from=build /load /usr/local/bin/load
ENTRYPOINT ["load"]
