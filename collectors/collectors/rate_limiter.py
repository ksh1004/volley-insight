"""한 서버에 보내는 요청 간격을 일정 이상으로 유지한다(NFR-01: KOVO에는 1초에 최대 1번).

KOVO 공식 사이트에 부담을 주지 않기 위한 약속이라, KOVO를 부르는 모든 코드는 이 제한기를 거친다.
  - RateLimiter: 한 프로세스 안에서만 간격을 지킨다(로컬 실행, 테스트).
  - FileRateLimiter: 같은 컴퓨터의 여러 프로세스가 잠금 파일 하나로 간격을 공유한다(컨테이너 운영).
"""
import os
import threading
import time


class RateLimiter:
    """한 프로세스 안에서의 요청 간격 제한. 스레드가 여러 개여도 잠금으로 순서를 지킨다."""

    def __init__(self, min_interval_sec: float, clock=time.monotonic, sleep=time.sleep):
        """min_interval_sec: 요청 사이 최소 간격(초). clock/sleep은 테스트에서 가짜 시계로 바꿔 끼우기 위한 것."""
        self.min_interval_sec = min_interval_sec
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._last = None  # 직전 요청 시각(처음엔 없음)

    def wait(self) -> None:
        """직전 호출 이후 `min_interval_sec`가 지날 때까지 기다린다. 요청 직전에 부른다."""
        with self._lock:
            now = self._clock()
            if self._last is not None:
                remaining = self.min_interval_sec - (now - self._last)
                if remaining > 0:
                    self._sleep(remaining)
                    now = self._clock()
            self._last = now


class FileRateLimiter:
    """같은 컴퓨터의 모든 프로세스가 공유하는 요청 간격 제한. 잠금 파일에 마지막 요청 시각을 적어 둔다.

    Airflow는 작업(task)마다 별도 프로세스로 돌기 때문에, 두 DAG가 동시에 수집하면 각자 1초 1회를
    지켜도 합치면 1초에 2회가 된다. 이 제한기를 쓰면 하나의 예산을 나눠 쓴다.
    (실시간 수집 컨테이너와 Airflow도 같은 볼륨의 잠금 파일을 공유한다.) fcntl을 쓰므로 Linux/macOS 전용.
    """

    def __init__(self, path: str, min_interval_sec: float, clock=time.time, sleep=time.sleep):
        """path: 잠금 파일 경로. 프로세스끼리 비교해야 하므로 시계는 벽시계(time.time)를 쓴다."""
        import fcntl  # noqa: F401  (Windows에서는 여기서 바로 실패시켜 잘못된 설정을 일찍 알린다)

        self.path = path
        self.min_interval_sec = min_interval_sec
        self._clock = clock
        self._sleep = sleep
        self._local = threading.Lock()  # 같은 프로세스 안의 스레드끼리도 순서를 지키기 위한 잠금

    def wait(self) -> None:
        """파일 잠금을 잡고, 파일에 적힌 마지막 요청 시각과 비교해 필요한 만큼 기다린 뒤 지금 시각을 기록한다."""
        import fcntl

        with self._local, open(self.path, "a+", encoding="ascii") as f:
            fcntl.flock(f, fcntl.LOCK_EX)  # 기다리는 동안에도 잠금을 쥐고 있어서, 다른 프로세스는 줄을 선다
            try:
                f.seek(0)
                text = f.read().strip()
                last = float(text) if text else 0.0
                remaining = self.min_interval_sec - (self._clock() - last)
                if remaining > 0:
                    self._sleep(remaining)
                f.seek(0)
                f.truncate()
                f.write(repr(self._clock()))
                f.flush()
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)


def default_limiter(min_interval_sec: float, lock_file: str | None):
    """잠금 파일이 설정돼 있으면(컨테이너 환경) 파일 기반 제한기, 아니면 프로세스 내 제한기를 돌려준다."""
    if lock_file and os.name == "posix":
        return FileRateLimiter(lock_file, min_interval_sec)
    return RateLimiter(min_interval_sec)
