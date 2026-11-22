FROM golang:1.27.4-bookworm@sha256:4fa7c968ba93f1635670522dbb01a2c78150443e1385746924566079f36570ea AS build
WORKDIR /src
COPY go.mod *.go Dockerfile ./
RUN go test ./... && CGO_ENABLED=0 go build -trimpath -o /out/upload-gate . \
 && sha256sum *.go go.mod Dockerfile > /out/sources.sha256

FROM debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc
LABEL org.examples.owner="iceberg-multi-system"
COPY --from=build /out/upload-gate /usr/local/bin/upload-gate
COPY --from=build /out/sources.sha256 /usr/local/share/compaction-upload-gate/sources.sha256
USER 10001
STOPSIGNAL SIGTERM
ENTRYPOINT ["upload-gate"]
