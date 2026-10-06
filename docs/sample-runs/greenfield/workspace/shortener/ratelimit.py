"""In-process token-bucket rate limiter (per client key)."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    def __init__(
        self,
        rate_per_minute: int,
        *,
        burst: int | None = None,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = 10_000,
    ) -> None:
        self.rate = rate_per_minute / 60.0
        self.capacity = float(burst if burst is not None else rate_per_minute)
        self._clock = clock
        self._max_keys = max_keys
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()
        self._lock = threading.Lock()

    def acquire(self, key: str) -> float:
        """Take one token. Returns 0 when allowed, else seconds until a token is available."""
        now = self._clock()
        with self._lock:
            bucket = self._buckets.pop(key, None) or _Bucket(self.capacity, now)
            bucket.tokens = min(self.capacity, bucket.tokens + (now - bucket.updated) * self.rate)
            bucket.updated = now
            self._buckets[key] = bucket
            while len(self._buckets) > self._max_keys:  # bound memory: drop least recent
                self._buckets.popitem(last=False)
            if bucket.tokens >= 1:
                bucket.tokens -= 1
                return 0.0
            return (1 - bucket.tokens) / self.rate
