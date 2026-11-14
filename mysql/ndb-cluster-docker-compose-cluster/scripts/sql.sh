#!/bin/sh
set -eu
node=$1
shift
docker compose exec -e MYSQL_PWD=local-root -T "$node" mysql -uroot -Dorders "$@"
