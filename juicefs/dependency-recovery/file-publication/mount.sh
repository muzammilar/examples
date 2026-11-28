#!/bin/bash
set -euo pipefail
test "${META_URL:?}" = 'postgres://juicefs@metadata:5432/juicefs?sslmode=disable&connect_timeout=5&max_open_conns=8&max_idle_conns=4'
test "${S3_BUCKET_URL:?}" = 'http://s3:8333/juicefs'
test "${AWS_REGION:?}" = us-east-1
test "${AWS_CONFIG_FILE:?}" = /dev/null
test "${AWS_SHARED_CREDENTIALS_FILE:?}" = /dev/null
mkdir -p /mnt/lab /tmp/jfs-cache
readonly=()
if [[ "${READ_ONLY:?}" = true ]]; then
    readonly=(--read-only)
elif [[ "$READ_ONLY" != false ]]; then
    exit 1
fi
juicefs mount --cache-dir=/tmp/jfs-cache --cache-size=0 --buffer-size=32 \
    --max-readahead=0 --prefetch=0 --open-cache=0s \
    --attr-cache=0s --entry-cache=0s --dir-entry-cache=0s \
    --get-timeout=5s --put-timeout=5s --io-retries=2 \
    --max-uploads=4 --max-downloads=4 --metrics=0.0.0.0:9567 \
    "${readonly[@]}" "$META_URL" /mnt/lab &
pid=$!
cleanup() {
    trap - EXIT INT TERM
    if mountpoint -q /mnt/lab; then
        fusermount3 -u /mnt/lab
    fi
    if kill -0 "$pid" 2>/dev/null; then
        kill -TERM "$pid"
    fi
    wait "$pid" || true
}
trap cleanup EXIT
trap 'cleanup; exit 0' INT TERM
wait "$pid"
