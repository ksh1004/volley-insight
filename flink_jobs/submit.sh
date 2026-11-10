#!/bin/sh
# PyFlink 작업을 제출한다. 단, 같은 이름의 작업이 이미 돌고 있으면 제출하지 않는다.
# 같은 작업이 두 개 돌면 같은 메시지를 둘 다 읽는다. 예: raw-archiver면 모든 메시지가 두 번 저장된다.
#
#   docker compose exec flink-jobmanager sh /opt/flink/usrlib/jobs/submit.sh raw_archiver.py raw-archiver
#   (인자 1: 작업 파일, 인자 2: 작업 이름 = 파일 안의 pipeline.name)
#
# flink-job-keeper 서비스(compose.yaml)도 이 스크립트를 1분마다 불러, PC나 Docker가 재시작돼도 작업이
# 저절로 다시 뜨게 한다. 이때 가장 최근의 완전한 체크포인트/세이브포인트(latest_checkpoint.py)에서 복원한다.
# 그러면 Kafka 오프셋과 쓰다 만 파일이 모두 이어진다. 커밋된 Kafka 오프셋만으로 새로 시작하면
# 마감 안 된 파일에 있던 내용을 잃는다.
set -u
script="$1"
name="$2"
jobmanager="${FLINK_JOBMANAGER:-}"
target=""
[ -n "$jobmanager" ] && target="-m $jobmanager"  # 다른 컨테이너에서 부를 때는 jobmanager 주소를 지정

# jobmanager에게 물어볼 수 없으면 아무것도 하지 않는다: '안 돌고 있다'고 추측하면 두 번째 복사본을 제출하게 된다
if ! jobs=$(flink list $target 2>/dev/null); then
  echo "job manager not reachable; not submitting"
  exit 1
fi
# 이미 실행 중(또는 시작 중·재시작 중)인지 작업 목록에서 이름으로 찾는다
if echo "$jobs" | grep -E " : ${name} \((RUNNING|CREATED|RESTARTING|INITIALIZING|SCHEDULED)\)" >/dev/null; then
  echo "${name} is already running; not submitting again."
  exit 0
fi

# 복원할 체크포인트를 찾는다. HDFS가 아직 준비 안 됐으면 다음 기회에 다시 시도한다
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
