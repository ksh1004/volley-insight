#!/bin/bash
# 하둡 컨테이너 시작 스크립트. 쓰는 법: entrypoint.sh namenode|datanode|resourcemanager|nodemanager
# namenode는 처음 시작할 때(데이터 폴더가 비어 있을 때)만 HDFS를 포맷한다. 그 밖의 인자는 그대로 실행.
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
