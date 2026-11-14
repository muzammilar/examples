#!/bin/sh
set -eu
socket=$(printf '/vt/vtdataroot/vt_%010d/mysql.sock' "$TABLET_UID")
mysqladmin --socket="$socket" -uvt_dba ping --silent
curl -fsS http://127.0.0.1:8080/debug/vars > /dev/null
