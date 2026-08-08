#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

CH_VERSION=$(awk -F': *' '/^ch_version:/{gsub(/"/,"",$2); print $2}' versions.yaml)
CHK_VERSION=$(awk -F': *' '/^chk_version:/{gsub(/"/,"",$2); print $2}' versions.yaml)

export CH_VERSION CHK_VERSION
envsubst < chi.yaml.tmpl > chi.yaml
envsubst < chk.yaml.tmpl > chk.yaml

echo "rendered chi.yaml with CH_VERSION=$CH_VERSION"
echo "rendered chk.yaml with CHK_VERSION=$CHK_VERSION"
