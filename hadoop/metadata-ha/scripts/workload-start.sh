#!/usr/bin/env bash
set -euo pipefail
export CLASSPATH="$(hadoop classpath --glob)"
exec /opt/hadoop/bin/files-workload "$@"
