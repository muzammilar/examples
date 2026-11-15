#!/bin/sh
set -eu
arch=${1:-all}
case "$arch" in
  all) architectures='duck-direct ch-direct ch-archive duck-archive ch-sharded';;
  duck-direct|ch-direct|ch-archive|duck-archive|ch-sharded) architectures=$arch;;
  *) echo 'ARCH must be all, duck-direct, ch-direct, ch-archive, duck-archive or ch-sharded' >&2; exit 2;;
esac
base=${RESULTS_DIR:-results/manual}
for architecture in $architectures; do
  export RESULTS_DIR="$base/$architecture"
  mkdir -p "$RESULTS_DIR"
  make "${2:-test-case}" ARCH="$architecture" RESULTS_DIR="$RESULTS_DIR"
done
