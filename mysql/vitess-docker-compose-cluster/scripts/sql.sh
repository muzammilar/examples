#!/bin/sh
set -eu
node=$1
shift
uid=${node#t}
socket=$(printf '/vt/vtdataroot/vt_%010d/mysql.sock' "$uid")
docker compose exec -T "$node" mysql --socket="$socket" -uvt_dba "$@"
