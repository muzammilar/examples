#!/usr/bin/env bash
set -euo pipefail
compose=(docker compose --project-name doris-replicated-orders)
mkdir -p results
"${compose[@]}" run --rm orders verify > results/before-crash.json
trap '"${compose[@]}" up --no-deps -d --wait be1' EXIT
"${compose[@]}" kill --signal SIGKILL be1
docker inspect doris-replicated-orders-be1-1 --format '{{json .State}}' > results/killed-backend.json
"${compose[@]}" run --rm orders verify > results/during-crash.json
"${compose[@]}" up --no-deps -d --wait be1
trap - EXIT
make up
"${compose[@]}" run --rm orders verify > results/after-crash.json
