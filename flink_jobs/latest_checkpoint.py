"""작업의 가장 최근 '완전한' 체크포인트 또는 세이브포인트 경로를 출력한다(`flink run -s`에 넘길 값, 없으면 빈 출력).

  python3 latest_checkpoint.py raw-archiver
    -> hdfs://namenode:9000/flink/checkpoints/raw-archiver/<작업 id>/chk-123

체크포인트 = Flink가 주기적으로 자동 저장하는 작업 상태, 세이브포인트 = 사람이 멈출 때 남기는 상태.
WebHDFS로 /flink/checkpoints/<이름>/*/chk-*/ 와 /flink/savepoints/<이름>/*/ 를 살펴, `_metadata` 파일이 있는
폴더만 고른다(종료 중에 끊긴 체크포인트에는 이 파일이 없어 복원할 수 없다).
여기서 복원하면 Kafka 오프셋 *과* 쓰다 만 파일까지 이어 가므로 재시작해도 잃는 것이 없다.
반대로 커밋된 Kafka 오프셋만으로 새로 시작하면 마감 안 된 파일에 있던 내용을 건너뛰게 된다.
"""
import json
import os
import sys
import urllib.error
import urllib.request

WEBHDFS = os.environ.get("WEBHDFS_URL", "http://namenode:9870/webhdfs/v1")  # HDFS의 HTTP API
FS = os.environ.get("HDFS_URI", "hdfs://namenode:9000")


def ls(path):
    """HDFS 폴더 목록(WebHDFS LISTSTATUS). 폴더가 없으면 빈 목록."""
    try:
        with urllib.request.urlopen(f"{WEBHDFS}{path}?op=LISTSTATUS&user.name=flink", timeout=10) as r:
            return json.load(r)["FileStatuses"]["FileStatus"]
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        raise


def candidates(name):
    """복원할 수 있는 (수정 시각, 경로) 후보를 모두 내보낸다.
    체크포인트는 작업 id 폴더 아래 chk-N 폴더가 한 단계 더 있고(nested), 세이브포인트는 바로 아래에 있다."""
    for base, nested in ((f"/flink/checkpoints/{name}", True), (f"/flink/savepoints/{name}", False)):
        for job in ls(base):
            if job["type"] != "DIRECTORY":
                continue
            dirs = ls(f"{base}/{job['pathSuffix']}") if nested else [job]
            parent = f"{base}/{job['pathSuffix']}" if nested else base
            for d in dirs:
                if d["type"] != "DIRECTORY" or (nested and not d["pathSuffix"].startswith("chk-")):
                    continue
                path = f"{parent}/{d['pathSuffix']}"
                meta = [f for f in ls(path) if f["pathSuffix"] == "_metadata"]
                if meta:
                    yield meta[0]["modificationTime"], path


def main():
    """가장 늦게 만들어진 후보의 전체 HDFS 경로를 출력한다."""
    found = sorted(candidates(sys.argv[1]))
    if found:
        print(f"{FS}{found[-1][1]}")


if __name__ == "__main__":
    main()
