#!/bin/bash
set -ex

# fix permissions, since the dictionary directory is not as root user
chown -R clickhouse:clickhouse /etc/clickhouse-server/

# create tables and insert data
clickhouse-client --multiline --multiquery --echo --format PrettyCompact --queries-file /ch-test/sql/01-*.sql
clickhouse-client --multiline --multiquery --echo --format PrettyCompact --queries-file /ch-test/sql/02-*.sql

# read the data
clickhouse-client --multiline --multiquery --echo --format PrettyCompact --queries-file /ch-test/sql/91-*.sql # 91-* and 92-*
clickhouse-client --multiline --multiquery --echo --format PrettyCompact --queries-file /ch-test/sql/92-*.sql # 91-* and 92-*
