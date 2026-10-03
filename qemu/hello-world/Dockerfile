# Build a static /init and a tiny initramfs for the guest ARCH, fetch a pinned kernel,
# then ship both with QEMU. Only Docker is needed on the host.
ARG DEBIAN_IMAGE=debian:trixie-slim

FROM ${DEBIAN_IMAGE} AS build
# guest architecture: x86_64 or aarch64
ARG ARCH=x86_64
# Debian 13 (trixie) cloud kernel, pinned on snapshot.debian.org (permanent URLs) with sha256
ARG KERNEL_VERSION=6.12.111+deb13
ARG KERNEL_DEB_VERSION=6.12.111-1
ARG KERNEL_SNAPSHOT=https://snapshot.debian.org/archive/debian-security/20260929T095154Z/pool/updates/main/l
ARG KERNEL_SHA256_AMD64=61e99b7ab064f2a64eb87f375adaa322d59c2cd252ba1f320e8706a24b9b90af
ARG KERNEL_SHA256_ARM64=d7c5c91a5fe4bbe59b63b9455044fdb6727b0833d1d878918fc0893388b2c781
RUN set -eu; \
    case "$ARCH" in \
      x86_64)  deb=amd64; sha=$KERNEL_SHA256_AMD64 ;; \
      aarch64) deb=arm64; sha=$KERNEL_SHA256_ARM64 ;; \
      *) echo "unsupported ARCH=$ARCH (x86_64 or aarch64)" >&2; exit 1 ;; \
    esac; \
    # native gcc when the build host matches the guest, otherwise a cross compiler
    if [ "$(dpkg --print-architecture)" = "$deb" ]; then cc="gcc libc6-dev"; \
    else cc="gcc-$(echo $ARCH | tr _ -)-linux-gnu libc6-dev-$deb-cross"; fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl cpio $cc; \
    rm -rf /var/lib/apt/lists/*; \
    f="linux-image-${KERNEL_VERSION}-cloud-${deb}_${KERNEL_DEB_VERSION}_${deb}.deb"; \
    curl -fsSL --retry 5 -o /tmp/kernel.deb \
      "$KERNEL_SNAPSHOT/linux-signed-$deb/$(echo "$f" | sed 's/+/%2B/g')"; \
    echo "$sha  /tmp/kernel.deb" | sha256sum -c -; \
    dpkg-deb --fsys-tarfile /tmp/kernel.deb | tar -xO --wildcards './boot/vmlinuz-*' > /vmlinuz; \
    rm /tmp/kernel.deb
WORKDIR /src
COPY main.c .
# static: the initramfs holds nothing but /init (no libc, no ld.so)
RUN set -eu; \
    $ARCH-linux-gnu-gcc -static -O2 -Wall -o init main.c; \
    mkdir -p root; install -m 0755 init root/init; \
    (cd root && find . | cpio -o -H newc -R 0:0 --quiet) | gzip -9 > /initramfs.cpio.gz; \
    ls -l /vmlinuz /initramfs.cpio.gz init

FROM ${DEBIAN_IMAGE}
ARG ARCH=x86_64
RUN set -eu; \
    case "$ARCH" in x86_64) pkg=qemu-system-x86 ;; aarch64) pkg=qemu-system-arm ;; esac; \
    apt-get update; \
    apt-get install -y --no-install-recommends $pkg; \
    rm -rf /var/lib/apt/lists/*
COPY --from=build /vmlinuz /initramfs.cpio.gz /boot/
COPY hello.sh /usr/local/bin/hello.sh
ENV ARCH=${ARCH}
ENTRYPOINT ["/usr/local/bin/hello.sh"]
