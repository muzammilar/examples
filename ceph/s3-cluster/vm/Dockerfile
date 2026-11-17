FROM ubuntu:24.04 AS packages
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates gpg genisoimage
ADD --checksum=sha256:46207b03a3645e7d6f83790dc6c0fddb6bd08fd5a21843845cb1f294d96d8c3e https://download.ceph.com/keys/release.asc /tmp/ceph.asc
RUN gpg --dearmor < /tmp/ceph.asc > /usr/share/keyrings/ceph.gpg \
 && echo 'deb [signed-by=/usr/share/keyrings/ceph.gpg] https://download.ceph.com/debian-tentacle/ noble main' > /etc/apt/sources.list.d/ceph.list \
 && apt-get update \
 && apt-get -o Dir::State::status=/dev/null --download-only --no-install-recommends -y install ceph-mon=20.2.5-1noble ceph-osd=20.2.5-1noble ceph-mgr=20.2.5-1noble radosgw=20.2.5-1noble ceph-volume=20.2.5-1noble lvm2 openssh-server iproute2 \
 && genisoimage -quiet -J -r -V CEPHPKGS -o /packages.iso /var/cache/apt/archives
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends qemu-system-arm qemu-efi-aarch64 qemu-utils cloud-image-utils openssh-client python3 ca-certificates \
 && rm -rf /var/lib/apt/lists/*
ADD --checksum=sha256:50e24e1fb79e89116e939d90aa7738f7af48c442fa1bc2510131e0b39c50348e https://cloud-images.ubuntu.com/noble/20261017/noble-server-cloudimg-arm64.img /opt/base.qcow2
COPY --from=packages /packages.iso /opt/packages.iso
COPY vm/boot.py /opt/lab/boot.py
COPY capacity.py /opt/lab/capacity.py
COPY native /opt/lab/native
ENTRYPOINT ["python3", "/opt/lab/boot.py"]
