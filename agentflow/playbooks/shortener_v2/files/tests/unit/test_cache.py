from shortener.cache import TTLCache


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_get_set_and_expiry():
    clock = Clock()
    cache = TTLCache(maxsize=10, ttl=30, clock=clock)
    cache.set("a", 1)
    assert cache.get("a") == 1
    clock.t = 30
    assert cache.get("a") is None


def test_lru_eviction_is_bounded():
    cache = TTLCache(maxsize=2, ttl=30, clock=Clock())
    cache.set("a", 1)
    cache.set("b", 2)
    cache.get("a")  # a is now most recent
    cache.set("c", 3)
    assert (cache.get("a"), cache.get("b"), cache.get("c")) == (1, None, 3)
    assert len(cache) == 2


def test_invalidate():
    cache = TTLCache(maxsize=2, ttl=30, clock=Clock())
    cache.set("a", 1)
    cache.invalidate("a")
    cache.invalidate("missing")
    assert cache.get("a") is None
