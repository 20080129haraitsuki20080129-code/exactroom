"""メモリ内のシンプルなレート制限。

外部サービス (Redis など) に依存しないため無料で動く。
複数プロセス / 複数インスタンスにスケールさせる場合は、
``RateLimiter`` を同じインタフェースの実装 (Redis など) に差し替える。
"""

from __future__ import annotations

import threading
import time
from collections import deque


class RateLimiter:
    """スライディングウィンドウ方式。"""

    def __init__(self, max_entries: int = 20000) -> None:
        self._buckets: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._max_entries = max_entries

    def hit(self, key: str, limit: int, window_sec: float) -> bool:
        """1 回分消費する。上限内なら ``True``、超過なら ``False``。"""
        if limit <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                if len(self._buckets) >= self._max_entries:
                    self._evict(now)
                bucket = deque()
                self._buckets[key] = bucket
            while bucket and now - bucket[0] > window_sec:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            return True

    def retry_after(self, key: str, window_sec: float) -> int:
        with self._lock:
            bucket = self._buckets.get(key)
            if not bucket:
                return 0
            return max(1, int(window_sec - (time.monotonic() - bucket[0])) + 1)

    def _evict(self, now: float) -> None:
        stale = [k for k, v in self._buckets.items() if not v or now - v[-1] > 3600]
        for key in stale[: self._max_entries // 2]:
            self._buckets.pop(key, None)
        if len(self._buckets) >= self._max_entries:
            self._buckets.clear()

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


limiter = RateLimiter()
