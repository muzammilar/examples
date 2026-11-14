# Build pinned upstream sources: the old Docker Hub images are no longer public.
FROM golang:1.25.1-bookworm AS minio
WORKDIR /src
RUN curl -fsSL https://github.com/minio/minio/archive/refs/tags/RELEASE.2025-10-15T17-29-55Z.tar.gz -o minio.tar.gz \
    && echo 'be6d0bd3696c3a13a35f02d3a0280b64319c67918b4501c5c3d87f96d000085c  minio.tar.gz' | sha256sum -c - \
    && tar -xzf minio.tar.gz --strip-components=1
RUN --mount=type=cache,target=/go/pkg/mod GOMAXPROCS=2 CGO_ENABLED=0 go build -p 2 -trimpath \
    -ldflags '-X github.com/minio/minio/cmd.Version=RELEASE.2025-10-15T17-29-55Z -X github.com/minio/minio/cmd.ReleaseTag=RELEASE.2025-10-15T17-29-55Z' -o /out/minio .
FROM golang:1.25.1-bookworm AS mc
WORKDIR /src
RUN curl -fsSL https://github.com/minio/mc/archive/refs/tags/RELEASE.2025-08-13T08-35-41Z.tar.gz -o mc.tar.gz \
    && echo '29db22500374169a43951c7cef09daf19e7291ea5ba00ac10f321371b0a35b32  mc.tar.gz' | sha256sum -c - \
    && tar -xzf mc.tar.gz --strip-components=1
RUN --mount=type=cache,target=/go/pkg/mod GOMAXPROCS=2 CGO_ENABLED=0 go build -p 2 -trimpath -o /out/mc .
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home app && install -d -o app -g app /data
COPY --from=minio /out/minio /usr/local/bin/minio
COPY --from=mc /out/mc /usr/local/bin/mc
USER app
ENTRYPOINT ["minio"]
