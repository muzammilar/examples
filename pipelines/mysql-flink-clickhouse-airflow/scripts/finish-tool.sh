#!/bin/sh
# Preserve kernel counters before a one-shot container is removed.
set -u
status=0
"$@" || status=$?
for metric in cpu.stat memory.peak memory.current io.stat; do
    printf '\nCGROUP %s\n' "$metric"
    cat "/sys/fs/cgroup/$metric" || printf 'unavailable\n'
done
exit "$status"
