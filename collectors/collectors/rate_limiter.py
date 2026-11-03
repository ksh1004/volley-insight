"""Keeps requests to one host at or below a fixed rate (NFR-01: at most 1 request per second to KOVO)."""
import threading
import time


class RateLimiter:
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
