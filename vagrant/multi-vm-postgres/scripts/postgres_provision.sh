#!/bin/bash

# source: https://www.postgresql.org/download/linux/ubuntu/

set -xe

# Install the latest version of PostgreSQL.
# If you want a specific version, use 'postgresql-12' or similar instead of 'postgresql':
DEBIAN_FRONTEND=noninteractive apt -y install postgresql-"$POSTGRES_VERSION"
