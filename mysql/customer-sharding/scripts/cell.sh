#!/bin/sh
set -eu
ctl() { /vt/bin/vtctldclient --server vtctld:15999 "$@"; }
if ! ctl GetCellInfo zone1 >/dev/null 2>&1; then
 ctl AddCellInfo --root /vitess/zone1 --server-address etcd0:2379,etcd1:2379,etcd2:2379 zone1
fi
if ! ctl GetKeyspace orders >/dev/null 2>&1; then
 ctl CreateKeyspace --durability-policy semi_sync orders
fi
