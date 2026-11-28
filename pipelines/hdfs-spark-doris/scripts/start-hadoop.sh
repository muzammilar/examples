#!/usr/bin/env bash
set -euo pipefail
case "$1" in
  namenode)
    if [[ ! -f /data/name/current/VERSION ]]; then
      if [[ -n "$(find /data/name -mindepth 1 -print -quit)" ]]; then
        echo 'Refusing to format a nonempty NameNode directory' >&2
        exit 1
      fi
      hdfs namenode -format -nonInteractive
    fi
    exec hdfs namenode
    ;;
  datanode) exec hdfs datanode ;;
  *) exec "$@" ;;
esac
