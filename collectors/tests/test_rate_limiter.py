"""요청 간격 제한기 테스트: KOVO에 초당 1회를 넘겨 요청하지 않도록 호출 사이를 띄우는지 확인한다.

FakeClock이 시간을 흉내 내므로 실제로 기다리지 않는다(sleep하면 시계만 앞으로 간다).
"""
from collectors.rate_limiter import RateLimiter


class FakeClock:
    """가짜 시계. now()는 현재 시각, sleep()은 기다린 시간을 slept에 기록하고 시계를 그만큼 앞으로 돌린다."""
    def __init__(self):
        self.t = 100.0
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, sec):
        self.slept.append(round(sec, 3))
        self.t += sec


def test_first_call_does_not_wait():
    """첫 요청은 기다리지 않는다."""
    c = FakeClock()
    RateLimiter(1.0, clock=c.now, sleep=c.sleep).wait()
    assert c.slept == []


def test_back_to_back_calls_are_spaced_by_interval():
    """0.3초 만에 다시 부르면 남은 0.7초를 기다린다."""
    c = FakeClock()
    lim = RateLimiter(1.0, clock=c.now, sleep=c.sleep)
    lim.wait()
    c.t += 0.3
    lim.wait()
    assert c.slept == [0.7]


def test_no_wait_when_interval_already_passed():
    """이미 간격(1초)보다 오래 지났으면 기다리지 않는다."""
    c = FakeClock()
    lim = RateLimiter(1.0, clock=c.now, sleep=c.sleep)
    lim.wait()
    c.t += 2.5
    lim.wait()
    assert c.slept == []


import os  # noqa: E402

import pytest  # noqa: E402

from collectors.rate_limiter import FileRateLimiter, default_limiter  # noqa: E402

# 파일 잠금(fcntl)은 Linux/macOS에만 있어 Windows에서는 건너뛴다(실제로는 Airflow 컨테이너(Linux)에서 쓰인다)
posix_only = pytest.mark.skipif(os.name != "posix", reason="fcntl lock file is Linux/macOS only")


@posix_only
def test_file_limiter_shares_spacing_between_instances(tmp_path):
    """같은 잠금 파일을 쓰는 제한기 둘은 하나처럼 동작한다(Airflow 작업 프로세스 둘이 동시에 KOVO를 부르는 상황)."""
    c = FakeClock()
    path = str(tmp_path / "kovo.lock")
    a = FileRateLimiter(path, 1.0, clock=c.now, sleep=c.sleep)
    b = FileRateLimiter(path, 1.0, clock=c.now, sleep=c.sleep)
    a.wait()
    c.t += 0.25
    b.wait()
    assert c.slept == [0.75]


def test_default_limiter_falls_back_without_lock_file():
    """잠금 파일 경로가 없으면 프로세스 안에서만 간격을 지키는 일반 제한기를 쓴다."""
    assert isinstance(default_limiter(1.0, ""), RateLimiter)
