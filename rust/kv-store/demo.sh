#!/usr/bin/env bash
# Walks through replication, failover, and rebuilding a node that lost its
# disk. Run it with `make demo`, which starts the cluster first.
set -euo pipefail
cd "$(dirname "$0")"

# Hide compose's progress output, but keep the client's own errors.
export COMPOSE_PROGRESS=quiet
kv() { docker compose run --rm --no-TTY client "$@"; }
step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

wait_for_key() { # node key
  for _ in $(seq 30); do
    kv get "$2" --local "$1:7000" >/dev/null 2>&1 && return
    sleep 1
  done
  echo "timed out waiting for $1 to have $2" >&2
  return 1
}

step "The cluster"
kv status

step "Writing 1000 keys through the leader, enough to trigger a snapshot"
kv fill --count 1000
kv get key-42

step "Load test: concurrent writes, then linearizable reads, through the leader"
kv bench --requests 2000 --concurrency 32

leader=$(kv leader)
leader=${leader%%:*}
step "Stopping the leader, $leader"
docker compose stop "$leader"

step "Writing with two nodes left: a new leader takes over within a second"
kv put after-failover yes
kv status || true

step "Restarting $leader: it rejoins as a follower and catches up"
docker compose start "$leader"
wait_for_key "$leader" after-failover
kv get after-failover --local "$leader:7000"

# Any follower but kv1, which has KV_BOOTSTRAP set: with an empty disk it
# would form a new cluster of its own.
leader=$(kv leader)
victim=kv2
[[ $leader == kv2:* ]] && victim=kv3
step "Wiping $victim's disk"
docker compose stop "$victim"
docker compose run --rm --no-TTY --no-deps --entrypoint sh "$victim" -c 'rm -rf /data/*'
docker compose start "$victim"

step "The leader rebuilds $victim from its snapshot, then the log after it"
wait_for_key "$victim" after-failover
kv get key-42 --local "$victim:7000"
kv status
