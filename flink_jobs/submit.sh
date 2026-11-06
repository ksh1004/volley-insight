#!/bin/sh
# Submit a PyFlink job unless a job with the same name is already running.
# Two copies of a job read the same messages, so raw-archiver would store every message twice.
#
#   docker compose exec flink-jobmanager sh /opt/flink/usrlib/jobs/submit.sh raw_archiver.py raw-archiver
#
# Also used by the flink-job-keeper service (compose.yaml), which calls it every minute so the job comes
# back by itself after the PC or Docker restarts. It restores from the job's newest complete checkpoint or
# savepoint (latest_checkpoint.py): that resumes both the Kafka offsets and the half-written files.
# Starting fresh from committed Kafka offsets instead would lose what was in the unfinished files.
set -u
script="$1"
name="$2"
jobmanager="${FLINK_JOBMANAGER:-}"
target=""
[ -n "$jobmanager" ] && target="-m $jobmanager"

# If the job manager cannot be asked, do nothing: guessing "not running" would submit a second copy.
if ! jobs=$(flink list $target 2>/dev/null); then
  echo "job manager not reachable; not submitting"
  exit 1
fi
if echo "$jobs" | grep -E " : ${name} \((RUNNING|CREATED|RESTARTING|INITIALIZING|SCHEDULED)\)" >/dev/null; then
  echo "${name} is already running; not submitting again."
  exit 0
fi

if ! restore=$(python3 /opt/flink/usrlib/jobs/latest_checkpoint.py "$name"); then
  echo "cannot list checkpoints (HDFS not ready?); not submitting"
  exit 1
fi
if [ -n "$restore" ]; then
  echo "submitting ${name}, restoring from ${restore}"
  flink run $target -d -s "$restore" -py "/opt/flink/usrlib/jobs/${script}"
else
  echo "submitting ${name} (no checkpoint yet: starts from the committed Kafka offsets)"
  flink run $target -d -py "/opt/flink/usrlib/jobs/${script}"
fi
