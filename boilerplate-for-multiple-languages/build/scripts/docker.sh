#!/bin/bash

set -xe

# See Docs: https://docs.docker.com/engine/install/ubuntu/
# repo setup
apt-get update
apt-get install -y ca-certificates curl gnupg lsb-release

# gpg key
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg

# add repo
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# install docker
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io

# add vagrant user to docker
groupadd docker
usermod -aG docker vagrant
