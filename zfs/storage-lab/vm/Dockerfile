FROM ubuntu:24.04@sha256:9c7f200c07e3d4a03e7249d2c16bf66a51120fb876aa2242cf31451412543289
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
 qemu-system-arm qemu-efi-aarch64 qemu-utils cloud-image-utils openssh-client python3 ca-certificates \
 && rm -rf /var/lib/apt/lists/*
ADD --checksum=sha256:50e24e1fb79e89116e939d90aa7738f7af48c442fa1bc2510131e0b39c50348e https://cloud-images.ubuntu.com/noble/20261017/noble-server-cloudimg-arm64.img /opt/base.qcow2
ARG ZFS_PACKAGE_VERSION=2.2.2-0ubuntu9.5
ARG FIO_PACKAGE_VERSION=3.36-1build2
# QEMU already installed these fio dependencies; download-only would omit them.
RUN apt-get update && mkdir -p /opt/debs && cd /opt/debs \
 && apt-get download zfsutils-linux=${ZFS_PACKAGE_VERSION} libnvpair3linux=${ZFS_PACKAGE_VERSION} libuutil3linux=${ZFS_PACKAGE_VERSION} libzfs4linux=${ZFS_PACKAGE_VERSION} libzpool5linux=${ZFS_PACKAGE_VERSION} \
 && apt-get install -y --download-only --no-install-recommends fio=${FIO_PACKAGE_VERSION} \
 && apt-get download libpmem1 libdaxctl1 libndctl6 librdmacm1t64 libibverbs1 libnl-3-200 libnl-route-3-200 libjson-c5 libkmod2 \
 && cp /var/cache/apt/archives/*.deb /opt/debs/ \
 && tar czf /opt/packages.tar.gz -C /opt/debs . \
 && rm -rf /var/lib/apt/lists/*
ARG LOCAL_UID=1000
RUN if ! getent passwd "${LOCAL_UID}" >/dev/null; then useradd --no-create-home --uid "${LOCAL_UID}" labctl; fi
LABEL org.examples.owner="zfs-storage-lab"
COPY vm/boot.py /opt/lab/boot.py
COPY app /opt/lab/app
ENTRYPOINT ["python3", "/opt/lab/boot.py"]
