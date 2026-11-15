#!/bin/sh
set -eu
case "$1" in
  seed|benchmark|churn|watch) exec /usr/local/bin/workload "$@" ;;
  *) exec python /app/workload.py "$@" ;;
esac
