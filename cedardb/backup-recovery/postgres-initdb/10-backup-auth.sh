#!/bin/sh
set -eu
: "${PGDATA:?}"
# No host ports are published; use an authenticated, replication-only identity.
rule='host replication backup_agent all scram-sha-256'
if ! grep -Fqx "$rule" "$PGDATA/pg_hba.conf"; then
    printf '\n%s\n' "$rule" >> "$PGDATA/pg_hba.conf"
fi
