from collectors.rate_limiter import RateLimiter


class FakeClock:
    def __init__(self):
        self.t = 100.0
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, sec):
        self.slept.append(round(sec, 3))
        self.t += sec


def test_first_call_does_not_wait():
    c = FakeClock()
    RateLimiter(1.0, clock=c.now, sleep=c.sleep).wait()
    assert c.slept == []


def test_back_to_back_calls_are_spaced_by_interval():
    c = FakeClock()
    lim = RateLimiter(1.0, clock=c.now, sleep=c.sleep)
    lim.wait()
    c.t += 0.3
    lim.wait()
    assert c.slept == [0.7]


def test_no_wait_when_interval_already_passed():
    c = FakeClock()
    lim = RateLimiter(1.0, clock=c.now, sleep=c.sleep)
    lim.wait()
    c.t += 2.5
    lim.wait()
    assert c.slept == []


import os  # noqa: E402

import pytest  # noqa: E402

from collectors.rate_limiter import FileRateLimiter, default_limiter  # noqa: E402

posix_only = pytest.mark.skipif(os.name != "posix", reason="fcntl lock file is Linux/macOS only")


@posix_only
def test_file_limiter_shares_spacing_between_instances(tmp_path):
    """Two limiters on the same file behave like one (two Airflow task processes)."""
    c = FakeClock()
    path = str(tmp_path / "kovo.lock")
    a = FileRateLimiter(path, 1.0, clock=c.now, sleep=c.sleep)
    b = FileRateLimiter(path, 1.0, clock=c.now, sleep=c.sleep)
    a.wait()
    c.t += 0.25
    b.wait()
    assert c.slept == [0.75]


def test_default_limiter_falls_back_without_lock_file():
    assert isinstance(default_limiter(1.0, ""), RateLimiter)
