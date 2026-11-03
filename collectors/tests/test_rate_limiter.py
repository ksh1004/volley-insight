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
