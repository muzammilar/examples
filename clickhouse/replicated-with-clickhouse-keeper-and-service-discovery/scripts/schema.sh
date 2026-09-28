#!/usr/bin/env bash
# Create the Replicated databases on every current data node, then the tables once. Idempotent.
set -euo pipefail
cd "$(dirname "$0")/.."
. scripts/lib.sh
for host in $(members | awk '{print $2}'); do ch "$host" < sql/1-databases.sql; done
# DDL in a Replicated database prints one status row per replica; hide it
ch clickhouse-server-01 < sql/2-schema-base-tables.sql >/dev/null
ch clickhouse-server-01 < sql/3-schema-distributed-tables.sql >/dev/null
