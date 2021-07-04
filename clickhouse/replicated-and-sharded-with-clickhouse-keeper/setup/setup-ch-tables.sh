#!/bin/bash
set -ex

# create tables
# clickhouse-client --multiline --multiquery --format Pretty < /ch-replica-sql/1-*.sql
# clickhouse-client --multiline --multiquery --format Pretty < /ch-replica-sql/2-*.sql

# insert data into the tables
# clickhouse-client --multiline --multiquery --format Pretty < /ch-replica-sql/3-insert.sql
