#!/bin/bash

set -xe

# This is a hack to add env variables to the guest vm as well
echo "export KAFKA_VERSION=$KAFKA_VERSION" >> /home/vagrant/.bashrc
echo "export KAFKA_DIR=$KAFKA_DIR" >> /home/vagrant/.bashrc
echo "export KAFKA_HOME=$KAFKA_HOME" >> /home/vagrant/.bashrc

# install java
apt update
apt install -y default-jre

# download kafka
wget https://archive.apache.org/dist/kafka/$KAFKA_VERSION/$KAFKA_DIR.tgz
mkdir "$KAFKA_HOME"
tar -xzf "$KAFKA_DIR".tgz --directory "$KAFKA_HOME" --strip-components=1
