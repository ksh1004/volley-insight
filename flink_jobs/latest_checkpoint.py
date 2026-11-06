"""Print the newest complete checkpoint or savepoint of a job, for `flink run -s` (empty output if none).

  python3 latest_checkpoint.py raw-archiver
    -> hdfs://namenode:9000/flink/checkpoints/raw-archiver/<job id>/chk-123

Looks under /flink/checkpoints/<name>/*/chk-*/ and /flink/savepoints/<name>/*/ through WebHDFS and keeps
only directories that contain `_metadata` (a checkpoint cut off by a shutdown has none).
Restoring from it resumes the Kafka offsets *and* the half-written part files, so a restart loses nothing;
starting fresh from the committed Kafka offsets would skip whatever sat in the unfinished files.
"""
import json
import os
import sys
import urllib.error
import urllib.request

WEBHDFS = os.environ.get("WEBHDFS_URL", "http://namenode:9870/webhdfs/v1")
FS = os.environ.get("HDFS_URI", "hdfs://namenode:9000")


def ls(path):
    try:
        with urllib.request.urlopen(f"{WEBHDFS}{path}?op=LISTSTATUS&user.name=flink", timeout=10) as r:
            return json.load(r)["FileStatuses"]["FileStatus"]
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        raise


def candidates(name):
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
    found = sorted(candidates(sys.argv[1]))
    if found:
        print(f"{FS}{found[-1][1]}")


if __name__ == "__main__":
    main()
