# Compile the pinned Citus release natively; upstream's image is amd64-only.
FROM postgres:18.0-bookworm AS citus
ARG PG_DEBIAN_VERSION=18.7-1.pgdg12+2
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential postgresql-18=${PG_DEBIAN_VERSION} postgresql-server-dev-18=${PG_DEBIAN_VERSION} libcurl4-openssl-dev libssl-dev \
    liblz4-dev libzstd-dev libkrb5-dev curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN curl -fsSL https://github.com/citusdata/citus/archive/refs/tags/v14.2.0.tar.gz -o citus.tar.gz \
    && echo 'df221da519cea3740b3a538b846ce0ce5bdc082c5f05321f0361b8f5edc57ff7  citus.tar.gz' | sha256sum -c - \
    && tar -xzf citus.tar.gz --strip-components=1 \
    && ./configure && make -j4 && make install DESTDIR=/install
FROM postgres:18.0-bookworm
ARG PG_DEBIAN_VERSION=18.7-1.pgdg12+2
COPY --from=citus /install/ /
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-18=${PG_DEBIAN_VERSION} python3-venv curl libcurl4 \
    && python3 -m venv /opt/patroni \
    && /opt/patroni/bin/pip install --no-cache-dir 'patroni[etcd3]==4.0.6' 'psycopg2-binary==2.9.10' \
    && rm -rf /var/lib/apt/lists/* \
    && install -d -o postgres -g postgres /var/lib/postgresql/patroni \
    && printf '*:*:*:postgres:local-postgres\n' > /var/lib/postgresql/.pgpass \
    && chown postgres:postgres /var/lib/postgresql/.pgpass && chmod 600 /var/lib/postgresql/.pgpass
COPY patroni.yml /etc/patroni.yml
USER postgres
ENTRYPOINT ["/opt/patroni/bin/patroni", "/etc/patroni.yml"]
