FROM postgres:17.6-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends python3-venv curl \
    && python3 -m venv /opt/patroni \
    && /opt/patroni/bin/pip install --no-cache-dir 'patroni[etcd3]==4.0.6' 'psycopg2-binary==2.9.10' \
    && rm -rf /var/lib/apt/lists/* \
    && install -d -o postgres -g postgres /var/lib/postgresql/patroni
COPY patroni.yml /etc/patroni.yml
USER postgres
ENTRYPOINT ["/opt/patroni/bin/patroni", "/etc/patroni.yml"]
