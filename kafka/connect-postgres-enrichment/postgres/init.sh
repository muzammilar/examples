#!/bin/sh
set -eu

customer_count=${CUSTOMER_COUNT:-10000}
case "$customer_count" in
    ''|*[!0-9]*) echo "CUSTOMER_COUNT must be a positive integer" >&2; exit 1 ;;
esac
if [ "$customer_count" -lt 1 ]; then
    echo "CUSTOMER_COUNT must be a positive integer" >&2
    exit 1
fi

psql --set ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    --set customer_count="$customer_count" \
    --file /tmp/init.sql
