#!/bin/bash

set -xe

apt update
apt install -y apt-transport-https ca-certificates dirmngr

# add ClickHouse repo
apt-key adv --keyserver hkp://keyserver.ubuntu.com:80 --recv 8919F6BD2B48D754
apt-key adv --keyserver hkp://keyserver.ubuntu.com:80 --recv 3E4AD4719DDE9A38
echo "deb https://packages.clickhouse.com/deb stable main" | sudo tee /etc/apt/sources.list.d/clickhouse.list

# install ClickHouse
apt update
DEBIAN_FRONTEND=noninteractive apt install -y clickhouse-client="$CLICKHOUSE_VERSION" clickhouse-server="$CLICKHOUSE_VERSION" clickhouse-common-static=$CLICKHOUSE_VERSION
clickhouse-server --version

# check if ClickHouse is running
systemctl start clickhouse-server
systemctl status clickhouse-server
