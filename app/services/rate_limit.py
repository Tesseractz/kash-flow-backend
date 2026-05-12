"""Sliding-window rate limiter for FastAPI middleware.

Two backends, chosen by env at startup:

  * **memory** (default) — per-process `deque` of timestamps. Fast and
    needs no external service, but the bucket lives in one worker's heap.
    With multiple uvicorn workers or horizontally scaled instances each
    process has its own bucket, so the effective limit is
    `MAX_REQUESTS * N_processes` per window. Fine for a single-instance
    deploy, OK as defense-in-depth at scale.

  * **redis** — atomic sorted-set per key. Counts are shared across every
    worker and instance that talks to the same Redis. Selected
    automatically when `REDIS_URL` is set in the env.

Window semantics: rolling window, not fixed bucket. Each request records
its timestamp; we drop timestamps older than `window_sec` before counting.

API used by the middleware:
    limiter = get_rate_limiter()
    allowed, retry_after = limiter.check(key)
    if not allowed: 429 with Retry-After=retry_after
"""
from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque
from typing import Optional, Tuple


# --------------------------------------------------------------------------
# Memory backend
# --------------------------------------------------------------------------
class MemoryRateLimiter:
    """Sliding window using a deque per key, in-process.

    Auto-purges keys that haven't been touched in 5 windows so a flood of
    short-lived IPs doesn't grow the dict unboundedly.
    """

    def __init__(self, *, window_sec: int, max_requests: int):
        self.window_sec = int(window_sec)
        self.max_requests = int(max_requests)
        self._buckets: dict[str, deque] = defaultdict(deque)
        self._last_seen: dict[str, float] = {}
        self._lock = threading.Lock()
        self._last_gc: float = time.monotonic()

    def _gc_if_needed(self, now: float) -> None:
        # Cheap, infrequent pass. Runs at most once per window.
        if now - self._last_gc < self.window_sec:
            return
        self._last_gc = now
        stale_cutoff = now - (self.window_sec * 5)
        stale_keys = [k for k, t in self._last_seen.items() if t < stale_cutoff]
        for k in stale_keys:
            self._buckets.pop(k, None)
            self._last_seen.pop(k, None)

    def check(self, key: str) -> Tuple[bool, int]:
        """Return (allowed, retry_after_seconds).

        When `allowed` is True, the request is recorded and counted toward
        the bucket. When False, `retry_after_seconds` is how long the
        caller should wait before retrying.
        """
        now = time.monotonic()
        with self._lock:
            self._gc_if_needed(now)
            q = self._buckets[key]
            cutoff = now - self.window_sec
            while q and q[0] < cutoff:
                q.popleft()
            self._last_seen[key] = now
            if len(q) >= self.max_requests:
                # Time until the oldest in-window request falls out.
                retry_after = max(1, int(self.window_sec - (now - q[0])) + 1)
                return False, retry_after
            q.append(now)
            return True, 0


# --------------------------------------------------------------------------
# Redis backend
# --------------------------------------------------------------------------
class RedisRateLimiter:
    """Sliding window using a Redis sorted set per key.

    Atomic via Lua so concurrent requests never race past the limit. The
    redis client is imported lazily so the rest of the app boots fine
    without the `redis` package installed.
    """

    _LUA = """
        local key = KEYS[1]
        local now = tonumber(ARGV[1])
        local window = tonumber(ARGV[2])
        local max_requests = tonumber(ARGV[3])
        local cutoff = now - window

        redis.call('ZREMRANGEBYSCORE', key, '-inf', cutoff)
        local count = redis.call('ZCARD', key)
        if count >= max_requests then
            local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
            local retry_after = window
            if #oldest >= 2 then
                retry_after = math.ceil(tonumber(oldest[2]) + window - now)
                if retry_after < 1 then retry_after = 1 end
            end
            return {0, retry_after}
        end
        redis.call('ZADD', key, now, now .. ':' .. math.random())
        redis.call('EXPIRE', key, window + 1)
        return {1, 0}
    """

    def __init__(self, *, redis_url: str, window_sec: int, max_requests: int):
        import redis  # type: ignore  # Imported lazily — package is optional

        self.window_sec = int(window_sec)
        self.max_requests = int(max_requests)
        # decode_responses keeps the script output as strings rather than bytes.
        self._client = redis.Redis.from_url(
            redis_url, decode_responses=True, socket_timeout=2.0
        )
        self._script = self._client.register_script(self._LUA)

    def check(self, key: str) -> Tuple[bool, int]:
        try:
            allowed, retry_after = self._script(
                keys=[f"ratelimit:{key}"],
                args=[time.time(), self.window_sec, self.max_requests],
            )
            return bool(int(allowed)), int(retry_after)
        except Exception:
            # Fail open — never block real traffic because Redis is down.
            # The memory limiter is still in front for defense-in-depth
            # if you stack them, but here we just allow on error.
            return True, 0


# --------------------------------------------------------------------------
# Factory
# --------------------------------------------------------------------------
_limiter: Optional[object] = None


def get_rate_limiter():
    """Return a process-wide singleton limiter.

    Memory by default; Redis when `REDIS_URL` is set AND the `redis`
    package can be imported. Logs which backend was chosen at startup.
    """
    global _limiter
    if _limiter is not None:
        return _limiter

    window_sec = int(os.getenv("RATE_LIMIT_WINDOW_SEC", "60"))
    max_requests = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "120"))
    redis_url = os.getenv("REDIS_URL", "").strip()

    if redis_url:
        try:
            _limiter = RedisRateLimiter(
                redis_url=redis_url,
                window_sec=window_sec,
                max_requests=max_requests,
            )
            print(
                f"[RateLimit] Using Redis backend "
                f"(window={window_sec}s, max={max_requests})"
            )
            return _limiter
        except Exception as e:
            print(
                f"[RateLimit] REDIS_URL set but redis backend failed to "
                f"initialize ({type(e).__name__}: {e}); falling back to memory."
            )

    _limiter = MemoryRateLimiter(window_sec=window_sec, max_requests=max_requests)
    print(
        f"[RateLimit] Using in-memory backend "
        f"(window={window_sec}s, max={max_requests}). "
        f"Set REDIS_URL to share counts across workers/instances."
    )
    return _limiter


def reset_for_tests() -> None:
    """Drop the cached limiter so each test gets a fresh one. Test-only."""
    global _limiter
    _limiter = None
