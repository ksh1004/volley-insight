"""Keeps requests to one host at or below a fixed rate (NFR-01: at most 1 request per second to KOVO)."""
import os
import threading
import time


class RateLimiter:
    """Spacing within one process."""

    def __init__(self, min_interval_sec: float, clock=time.monotonic, sleep=time.sleep):
        self.min_interval_sec = min_interval_sec
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._last = None

    def wait(self) -> None:
        """Block until at least `min_interval_sec` has passed since the previous call."""
        with self._lock:
            now = self._clock()
            if self._last is not None:
                remaining = self.min_interval_sec - (now - self._last)
                if remaining > 0:
                    self._sleep(remaining)
                    now = self._clock()
            self._last = now


class FileRateLimiter:
    """Spacing shared by every process on one machine, through a lock file holding the last request time.

    Airflow runs each task in its own process, so two DAGs collecting at the same time would each keep
    their own 1 req/s budget. With this limiter they share one. Linux/macOS only (fcntl).
    """

    def __init__(self, path: str, min_interval_sec: float, clock=time.time, sleep=time.sleep):
        import fcntl  # noqa: F401  (fail early on Windows)

        self.path = path
        self.min_interval_sec = min_interval_sec
        self._clock = clock
        self._sleep = sleep
        self._local = threading.Lock()

    def wait(self) -> None:
        import fcntl

        with self._local, open(self.path, "a+", encoding="ascii") as f:
            fcntl.flock(f, fcntl.LOCK_EX)  # held while sleeping, so waiting processes queue up
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
    """File-based limiter when a lock file is configured (containers), else an in-process one."""
    if lock_file and os.name == "posix":
        return FileRateLimiter(lock_file, min_interval_sec)
    return RateLimiter(min_interval_sec)
