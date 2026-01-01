#!/bin/bash
set -ex

# fix permissions, since the dictionary directory is not as root user
chown -R clickhouse:clickhouse /etc/clickhouse-server/
