#!/bin/bash

set -xe

# install elasticsearch, it should have inbuilt jvm
wget -qO - https://artifacts.elastic.co/GPG-KEY-elasticsearch | sudo apt-key add -

# install elasticsearch (not elasticsearch-oss since the company uses it)
apt install apt-transport-https

# non open-source elastic
echo "deb https://artifacts.elastic.co/packages/$ES_VERSION/apt stable main" | sudo tee -a /etc/apt/sources.list.d/elasticsearch.list
apt update && apt install -y elasticsearch

# elasticsearch configurations (add to exisiting configurations)
echo -e '-Xms256m\n-Xmx256m' > /etc/elasticsearch/jvm.options.d/overrides.options

# start elasticsearch
service elasticsearch start
service elasticsearch status
