# kdb+ `q` on Debian slim, shared by the kdb examples. The kdb+ 4.1 zips are served by KX's
# download portal without a login; running q needs a license, which entrypoint.sh installs at
# start-up from KDB_LICENSE_B64 / KDB_LICENSE_K4B64 or from a kc.lic / k4.lic mounted at /license.
#
# KDB_DIST=kdb+ (default): kdb+ KDB_VERSION (<major>/<build date>, pinned 4.1/2026.10.02).
# KDB_DIST=kdb-x: KDB-X q KDB_X_VERSION instead (pinned 5.0.20261002), for a KDB-X Community
# Edition key, in case kdb+ 4.1 does not accept it (see ../README.md). Same q code either way.
FROM debian:trixie-slim AS fetch
ARG KDB_DIST=kdb+
ARG KDB_VERSION=4.1/2026.10.02
ARG KDB_X_VERSION=5.0.20261002
ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl unzip \
	&& rm -rf /var/lib/apt/lists/*
# sha256 of the pinned zips; another version skips the check
RUN set -eu; \
	case "$TARGETARCH" in \
	arm64) p=l64arm ;; amd64) p=l64 ;; \
	*) echo "unsupported arch $TARGETARCH" >&2; exit 1 ;; \
	esac; \
	case "$KDB_DIST" in \
	kdb+) url="https://portal.dl.kx.com/assets/raw/kdb+/$KDB_VERSION/$p.zip"; \
		[ "$KDB_VERSION" = 4.1/2026.10.02 ] && pin=1 || pin=0; \
		case $p in \
		l64arm) sum=ed2fb1ed0c5169acb494d49fab1dd77c94013a53c25c200a81d7747f8a7a4362 ;; \
		l64) sum=22c40a1d62885acf73acb8f92f7181f2727d216cc45ea4e869a9506c40cd8708 ;; \
		esac ;; \
	kdb-x) url="https://portal.dl.kx.com/assets/raw/kdb-x/kdb-x/$KDB_X_VERSION/$p.zip"; \
		[ "$KDB_X_VERSION" = 5.0.20261002 ] && pin=1 || pin=0; \
		case $p in \
		l64arm) sum=87c8581a2787a7aef291a82ab26436c2b2f5df25b07e51316c0ee70a7a6aa70e ;; \
		l64) sum=ad8853fb35a74ff01a2a1cf3d4ae0f7e6cf93be2f24d597eb7ecc286865d1c0a ;; \
		esac ;; \
	*) echo "KDB_DIST must be kdb+ or kdb-x, not $KDB_DIST" >&2; exit 1 ;; \
	esac; \
	curl -fsSL -o /tmp/q.zip "$url"; \
	[ "$pin" = 0 ] || echo "$sum  /tmp/q.zip" | sha256sum -c -; \
	mkdir -p /opt/kx; unzip -q /tmp/q.zip -d /opt/kx; chmod 0755 "/opt/kx/$p/q"; \
	ln -s "/opt/kx/$p/q" /opt/kx/q-bin

FROM debian:trixie-slim
RUN useradd --uid 1000 --create-home kx && install -d -o kx -g kx /data
# QHOME layout as in the kdb+ install guide: q.k at the top, the binary under l64arm/ or l64/
COPY --from=fetch /opt/kx /opt/kx
RUN ln -s /opt/kx/q-bin /usr/local/bin/q
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
# QLIC: where entrypoint.sh puts the license (q looks for kc.lic / k4.lic there)
ENV QHOME=/opt/kx QLIC=/tmp/kx
USER kx
WORKDIR /data
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["q"]
