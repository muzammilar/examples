FROM golang:1.27.4-bookworm@sha256:4fa7c968ba93f1635670522dbb01a2c78150443e1385746924566079f36570ea AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN --mount=type=cache,target=/root/.cache/go-build CGO_ENABLED=1 go test ./... && CGO_ENABLED=1 go build -trimpath -o /out/lab . \
 && sha256sum *.go go.mod go.sum go.duckdb2.mod go.duckdb2.sum Dockerfile Dockerfile.duckdb2 > /out/sources.sha256
FROM debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates libstdc++6 \
 && rm -rf /var/lib/apt/lists/* && useradd --uid 10001 --create-home app \
 && install -d -o app -g app /opt/duckdb/extensions /results
COPY --from=build /out/lab /usr/local/bin/lab
COPY --from=build /out/sources.sha256 /usr/local/share/iceberg-lab/sources.sha256
LABEL org.examples.owner="iceberg-multi-system"
USER app
RUN lab extensions
ENTRYPOINT ["lab"]
