# KDB-X `q` on Debian slim, shared by the kdb-x examples. The binary is a public download from
# KX's portal (no account needed); running it needs a license, which entrypoint.sh installs at
# start-up from KDB_LICENSE_B64 / KDB_LICENSE_K4B64 or from a kc.lic / k4.lic mounted at /license.
FROM debian:trixie-slim AS fetch
ARG KDB_X_VERSION=5.0.20261002
ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl unzip \
	&& rm -rf /var/lib/apt/lists/*
# sha256 of the pinned zips (5.0.20261002); a different KDB_X_VERSION skips the check
RUN set -eu; \
	case "$TARGETARCH" in \
	arm64) p=l64arm sum=87c8581a2787a7aef291a82ab26436c2b2f5df25b07e51316c0ee70a7a6aa70e ;; \
	amd64) p=l64 sum=ad8853fb35a74ff01a2a1cf3d4ae0f7e6cf93be2f24d597eb7ecc286865d1c0a ;; \
	*) echo "unsupported arch $TARGETARCH" >&2; exit 1 ;; \
	esac; \
	curl -fsSL -o /tmp/q.zip "https://portal.dl.kx.com/assets/raw/kdb-x/kdb-x/$KDB_X_VERSION/$p.zip"; \
	[ "$KDB_X_VERSION" != 5.0.20261002 ] || echo "$sum  /tmp/q.zip" | sha256sum -c -; \
	unzip -q /tmp/q.zip -d /tmp/kx; install -m 0755 "/tmp/kx/$p/q" /usr/local/bin/q

FROM debian:trixie-slim
RUN useradd --uid 1000 --create-home kx && install -d -o kx -g kx /data
COPY --from=fetch /usr/local/bin/q /usr/local/bin/q
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
# QLIC: where entrypoint.sh puts the license (q looks for kc.lic / k4.lic there)
ENV QLIC=/tmp/kx
USER kx
WORKDIR /data
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["q"]
