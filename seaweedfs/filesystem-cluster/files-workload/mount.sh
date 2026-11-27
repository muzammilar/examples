#!/bin/bash
set -euo pipefail
test "${FILERS:?}" = 'filer0:8888,filer1:8888'
mkdir -p /mnt/lab /tmp/weed-cache
weed mount -filer="$FILERS" -dir=/mnt/lab -collection=filesystem-lab \
  -replication=200 -chunkSizeLimitMB=2 -cacheMetaTtlSec=1 \
  -cacheDir=/tmp/weed-cache -cacheCapacityMB=16 -readerCacheSizeMB=16 \
  -writeBufferSizeMB=16 -memoryLimitMB=256 -writebackCache=false -dlm=true &
pid=$!
cleanup() {
  trap - EXIT INT TERM
  fusermount3 -u /mnt/lab || true
  kill "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM
wait "$pid"
