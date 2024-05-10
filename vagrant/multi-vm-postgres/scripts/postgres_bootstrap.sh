#!/bin/bash

# source: whitefish

set -xe

# source: https://github.com/jackdb/pg-app-dev-vm/blob/master/Vagrant-setup/bootstrap.sh

PG_CONF="/etc/postgresql/$POSTGRES_VERSION/main/postgresql.conf"
PG_HBA="/etc/postgresql/$POSTGRES_VERSION/main/pg_hba.conf"
PG_DIR="/var/lib/postgresql/$POSTGRES_VERSION/main"

# Add postgres custom configurations
# verbose logging

# Edit postgresql.conf to change listen address to '*':
#sed -i "s/#listen_addresses = 'localhost'/listen_addresses = '*'/" "$PG_CONF"

# Append to pg_hba.conf to add password auth:
#echo "host    all             all             all                     md5" >> "$PG_HBA"

# Explicitly set default client_encoding
#echo "client_encoding = utf8" >> "$PG_CONF"

# Restart so that all new config is loaded:
#service postgresql restart

# make sure that postgres is up and running
pg_isready --timeout=30

# add custom users and databases
sudo -u postgres psql -c "CREATE ROLE $POSTGRES_USER WITH LOGIN PASSWORD '$POSTGRES_PASSWORD' SUPERUSER;" || true
sudo -u postgres psql -c "CREATE DATABASE $POSTGRES_DB_NAME WITH OWNER=$POSTGRES_USER;" || true
