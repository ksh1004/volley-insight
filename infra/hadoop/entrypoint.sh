#!/bin/bash
# Usage: entrypoint.sh namenode|datanode|resourcemanager|nodemanager
set -euo pipefail
role="${1:?role required}"

case "$role" in
  namenode)
    if [ ! -d /data/nn/current ]; then
      echo "formatting new namenode"
      hdfs namenode -format -nonInteractive -force
    fi
    exec hdfs namenode
    ;;
  datanode)        exec hdfs datanode ;;
  resourcemanager) exec yarn resourcemanager ;;
  nodemanager)     exec yarn nodemanager ;;
  *) exec "$@" ;;
esac
