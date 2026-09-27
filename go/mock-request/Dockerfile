ARG GO_VERSION=1.23
#build stage
FROM golang:${GO_VERSION}-alpine AS base
WORKDIR /httpmock
# download dependencies first for container cache and performance
COPY go.mod go.sum ./
RUN go mod download
COPY . .

# (re)generate the gomock mocks and run the unit tests
FROM base AS mockgen
RUN go generate ./...
CMD ["go", "test", "-v", "./..."]

# build a static binary
FROM base AS build
RUN CGO_ENABLED=0 go build -o /bin/httpmock ./cmd

# final image
FROM alpine:3.20 AS httpmock
RUN apk add --no-cache ca-certificates
COPY --from=build /bin/httpmock /bin/httpmock
ENTRYPOINT ["/bin/httpmock"]
CMD ["-url", "https://example.com"]
