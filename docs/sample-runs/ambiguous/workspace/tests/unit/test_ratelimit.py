from shortener.ratelimit import RateLimiter


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_allows_burst_then_limits_and_refills():
    clock = Clock()
    limiter = RateLimiter(60, burst=3, clock=clock)
    assert [limiter.acquire("k") for _ in range(3)] == [0.0, 0.0, 0.0]
    wait = limiter.acquire("k")
    assert 0 < wait <= 1.0
    clock.t += 1.0
    assert limiter.acquire("k") == 0.0


def test_keys_are_isolated():
    limiter = RateLimiter(60, burst=1, clock=Clock())
    assert limiter.acquire("a") == 0.0
    assert limiter.acquire("a") > 0
    assert limiter.acquire("b") == 0.0


def test_memory_is_bounded():
    limiter = RateLimiter(60, clock=Clock(), max_keys=10)
    for i in range(100):
        limiter.acquire(f"k{i}")
    assert len(limiter._buckets) == 10
