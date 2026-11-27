#!/usr/bin/env bash
set -euo pipefail
if [[ -n "${HDFS_NAMENODE_ID:-}" ]]; then
  python3 /opt/hadoop/lab-scripts/role_config.py
  export HADOOP_CONF_DIR=/tmp/hadoop-role-conf
fi
case "$1" in
  namenode)
    test -f /data/name/current/VERSION
    test -f "/lab-ssh/${HDFS_NAMENODE_ID}-hostkey"
    # Keep the node's fencing endpoint alive when its NN daemon dies. This models
    # a host's independent sshd; the native daemon tools own the NN PID/lifecycle.
    runuser -u hadoop --preserve-environment -- hdfs --daemon start namenode
    exec /usr/sbin/sshd -D -e -f /opt/hadoop/etc/hadoop/sshd.conf -h "/lab-ssh/${HDFS_NAMENODE_ID}-hostkey"
    ;;
  journalnode|datanode|zkfc)
    exec runuser -u hadoop --preserve-environment -- hdfs "$1"
    ;;
  *) exec "$@" ;;
esac
