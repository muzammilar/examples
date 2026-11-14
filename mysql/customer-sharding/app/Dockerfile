FROM golang:1.25.1-bookworm AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -o /workload .
FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /workload /workload
ENTRYPOINT ["/workload"]
