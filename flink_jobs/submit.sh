#!/bin/sh
# Submit a PyFlink job unless a job with the same name is already running.
# Two copies of a job read the same messages, so raw-archiver would store every message twice.
#
#   docker compose exec flink-jobmanager sh /opt/flink/usrlib/jobs/submit.sh raw_archiver.py raw-archiver
set -eu
script="$1"
name="$2"

if flink list -r 2>/dev/null | grep -q " : ${name} (RUNNING)"; then
  echo "${name} is already running; not submitting again."
  flink list -r 2>/dev/null | grep " : ${name} ("
  exit 0
fi
flink run -d -py "/opt/flink/usrlib/jobs/${script}"
