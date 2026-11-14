#!/bin/sh
set -eu
docker compose exec -T vtctld /vt/bin/vtctldclient --server localhost:15999 "$@"
