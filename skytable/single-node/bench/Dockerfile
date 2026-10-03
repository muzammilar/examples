# sky-bench, Skytable's own load generator, from the official v0.8.4 release bundle (the server
# image ships only skyd and skysh). The bundle has native linux arm64 and x86_64 builds.
FROM debian:trixie-slim
ARG TARGETARCH
ARG SKYTABLE_VERSION=0.8.4
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/* \
    && case "$TARGETARCH" in arm64) arch=arm64 ;; amd64) arch=x86_64 ;; *) exit 1 ;; esac \
    && curl -fsSL -o /tmp/sky.zip "https://github.com/skytable/skytable/releases/download/v${SKYTABLE_VERSION}/sky-bundle-v${SKYTABLE_VERSION}-${arch}-linux-gnu.zip" \
    && unzip -q /tmp/sky.zip sky-bench -d /usr/local/bin && rm /tmp/sky.zip
ENTRYPOINT ["sky-bench"]
