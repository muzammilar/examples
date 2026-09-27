# Build container for the funkpkg debian package.
# Usage: `make buildcontainer` (builds the image and runs `make deb` inside it)
FROM debian:bookworm

ENV DEBIAN_FRONTEND=noninteractive
ENV DEBEMAIL="example.email@foo.com"
ENV DEBFULLNAME="Muzammil Abdul Rehman"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        debhelper \
        devscripts \
        dh-sysuser \
        dh-virtualenv \
        lsb-release \
        make \
        python3 \
        python3-dev \
        python3-pip \
        python3-setuptools \
        python3-venv \
        python3-virtualenv \
        unzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /pkgbuilddir
CMD ["make", "deb"]
