#!/bin/bash

set -xe

# use non-interactive debian frontend to ease setup
export DEBIAN_FRONTEND="noninteractive"

# update
apt-get update

# Install some basic buildtools
apt-get install -y automake debhelper
apt-get install -y build-essential python3
