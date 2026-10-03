#!/bin/sh
# Patroni post_bootstrap: runs once, on the node that initializes the cluster, with a
# connection URL as $1. Replicas get the extensions through streaming replication.
set -e
psql "$1" -v ON_ERROR_STOP=1 -c 'CREATE EXTENSION IF NOT EXISTS timescaledb' \
	-c 'CREATE EXTENSION IF NOT EXISTS timescaledb_toolkit'
