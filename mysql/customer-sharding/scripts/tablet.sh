#!/bin/bash
set -euo pipefail
export VTROOT=/vt VTDATAROOT=/vt/vtdataroot
export EXTRA_MY_CNF=/config/low-memory.cnf
alias=$(printf 'zone1-%010d' "$TABLET_UID")
tablet_dir=$(printf 'vt_%010d' "$TABLET_UID")
mkdir -p "$VTDATAROOT/backups"
rm -f "$VTDATAROOT/$tablet_dir/mysql.sock" "$VTDATAROOT/$tablet_dir/mysql.sock.lock" "$VTDATAROOT/mysqlctld.sock"
/vt/bin/mysqlctld --tablet-uid "$TABLET_UID" --mysql-port 3306 \
 --socket-file "$VTDATAROOT/mysqlctld.sock" --init-db-sql-file /vt/config/init_db.sql --logtostderr &
mysql_pid=$!
/vt/bin/vttablet --topo-implementation etcd2 \
 --topo-global-server-address etcd0:2379,etcd1:2379,etcd2:2379 --topo-global-root /vitess/global \
 --tablet-path "$alias" --tablet-hostname "$HOSTNAME" --init-keyspace orders --init-shard "$SHARD" \
 --init-tablet-type replica --mysqlctl-socket "$VTDATAROOT/mysqlctld.sock" \
 --port 8080 --grpc-port 15999 --health-check-interval 1s \
 --service-map grpc-queryservice,grpc-tabletmanager --backup-storage-implementation file \
 --file-backup-storage-root "$VTDATAROOT/backups" --restore-from-backup --logtostderr &
tablet_pid=$!
cleanup() { kill "$tablet_pid" "$mysql_pid" 2>/dev/null || true; wait || true; }
trap cleanup EXIT INT TERM
set +e
wait -n "$mysql_pid" "$tablet_pid"
status=$?
exit "$status"
