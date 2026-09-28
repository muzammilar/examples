#!/usr/bin/env bash
# Walks through the cluster: membership, who owns which key, load with and
# without keys, and what happens when a node goes away. Run it with
# `make demo`, which starts the cluster first.
set -euo pipefail
cd "$(dirname "$0")"

export COMPOSE_PROGRESS=quiet
send() { printf '%b\n' "$*" | docker compose run --rm --no-TTY --no-deps client; }
bench() { docker compose run --rm --no-TTY --no-deps bench "$@"; }
step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
keys=(user1 user2 user3 user4 user5 user6)

owners() {
  for key in "${keys[@]}"; do
    printf '%-6s ' "$key"
    send "owner $key"
  done
}

# Waits until the cluster's member list does or doesn't include a node.
wait_for_membership() { # node present|absent
  for _ in $(seq 30); do
    if send nodes | grep -q "$1"; then [[ $2 == present ]] && return; else [[ $2 == absent ]] && return; fi
    sleep 1
  done
  echo "timed out waiting for $1 to be $2" >&2
  return 1
}

step "Cluster members"
send nodes

step "Key owners, asked through different nodes"
owners

step "One job of each kind"
send 'fib 90\nsleep 100 key=user1\ncrash\nsleep 10000\nstatus\nnope'

step "Detached job: submit, hang up, collect later"
id=$(send 'submit sleep 2000 key=user1' | awk '{print $3}')
echo "job $id"
send "result $id"
sleep 2.5
send "result $id"

step "Unkeyed 10ms jobs, spread over every node"
bench --requests 2000 --task 'sleep 10'

step "Keyed 10ms jobs, all on one owner"
bench --requests 1000 --task 'sleep 10 key=user1'

owner=$(send 'owner user1' | awk '{print $2}')
host=${owner#*@}
step "Stopping $host, owner of user1"
docker compose stop "$host"
wait_for_membership "$owner" absent
send nodes
owners
send 'fib 10 key=user1'

step "Restarting $host"
docker compose start "$host"
wait_for_membership "$owner" present
send nodes
owners
