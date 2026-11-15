FROM golang:1.27.4-bookworm AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=1 go build -trimpath -o /out/lab .
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates libstdc++6 \
 && rm -rf /var/lib/apt/lists/* && useradd --uid 10001 --create-home app \
 && install -d -o app -g app /opt/duckdb/extensions /results
COPY --from=build /out/lab /usr/local/bin/lab
USER app
RUN lab extensions
ENTRYPOINT ["lab"]
