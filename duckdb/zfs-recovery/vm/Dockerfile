FROM ubuntu:24.04@sha256:9c7f200c07e3d4a03e7249d2c16bf66a51120fb876aa2242cf31451412543289
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    qemu-system-arm qemu-efi-aarch64 qemu-utils cloud-image-utils openssh-client \
    python3 python3-venv ca-certificates procps \
 && rm -rf /var/lib/apt/lists/*
ADD --checksum=sha256:50e24e1fb79e89116e939d90aa7738f7af48c442fa1bc2510131e0b39c50348e https://cloud-images.ubuntu.com/noble/20261017/noble-server-cloudimg-arm64.img /opt/base.qcow2
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir duckdb==1.5.5 \
 && mkdir -p /opt/wheels /opt/duckdb_extensions \
 && /opt/venv/bin/pip download --no-deps duckdb==1.5.5 -d /opt/wheels
RUN /opt/venv/bin/python -c "import duckdb; c=duckdb.connect(); c.execute(\"SET extension_directory='/opt/duckdb_extensions'\"); c.execute(\"INSTALL quack FROM 'https://extensions.duckdb.org'\"); c.execute('LOAD quack')" \
 && tar czf /opt/extensions.tar.gz -C /opt duckdb_extensions
ARG ZFS_PACKAGE_VERSION=2.2.2-0ubuntu9.5
RUN apt-get update && mkdir -p /opt/zfs-debs && cd /opt/zfs-debs \
 && apt-get download zfsutils-linux=${ZFS_PACKAGE_VERSION} libnvpair3linux=${ZFS_PACKAGE_VERSION} libuutil3linux=${ZFS_PACKAGE_VERSION} libzfs4linux=${ZFS_PACKAGE_VERSION} libzpool5linux=${ZFS_PACKAGE_VERSION} \
 && tar czf /opt/zfs-debs.tar.gz -C /opt/zfs-debs . \
 && tar czf /opt/python-runtime.tar.gz -C /opt venv \
 && rm -rf /var/lib/apt/lists/*
ARG LOCAL_UID=1000
RUN if ! getent passwd "${LOCAL_UID}" >/dev/null; then useradd --no-create-home --uid "${LOCAL_UID}" labctl; fi
RUN printf '%s  %s\n' \
    '41b2b9292bfb860c5ca8c5f818f9dd7a2c6bc24f9c750cffbc3169286fe59f08' '/opt/duckdb_extensions/v1.5.5/linux_arm64/quack.duckdb_extension' \
    'eba6e263e395a83966090f1f11ade63630b1b21422f0f2813858d179d42ea1e9' '/opt/duckdb_extensions/v1.5.5/linux_arm64/httpfs.duckdb_extension' | sha256sum --check -
COPY vm/boot.py /opt/lab/boot.py
COPY app /opt/lab/app
ENTRYPOINT ["python3", "/opt/lab/boot.py"]
