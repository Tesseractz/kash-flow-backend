"""Rate limiter tests — middleware integration + both backends."""
import importlib
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import app.services.rate_limit as rl_mod


@pytest.fixture
def app_with_small_rate_limit(monkeypatch):
    """Reload the limiter module after setting tight env caps."""
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SEC", "60")
    monkeypatch.setenv("RATE_LIMIT_MAX_REQUESTS", "3")
    monkeypatch.delenv("REDIS_URL", raising=False)

    # Drop any cached limiter so the new env is picked up.
    rl_mod.reset_for_tests()

    import app.main as main_mod
    importlib.reload(main_mod)
    yield main_mod.app

    # Reset for any later tests.
    rl_mod.reset_for_tests()


def test_rate_limit_blocks_after_threshold(app_with_small_rate_limit):
    """4th /billing/checkout from same IP is 429 + Retry-After."""
    from app.api.deps import RequestContext, get_current_context

    client = TestClient(app_with_small_rate_limit)

    app_with_small_rate_limit.dependency_overrides[get_current_context] = lambda: RequestContext(
        user_id="u1", store_id="s1", role="admin"
    )

    with patch("app.db.supabase.get_supabase_client") as mock_supa, \
         patch("app.clients.paystack.initialize_transaction") as mock_init:
        mock_supa.return_value = MagicMock()
        mock_init.return_value = {
            "url": "https://checkout.paystack.com/test",
            "reference": "ref_test",
        }

        headers = {"x-forwarded-for": "1.2.3.4"}
        payload = {"plan": "pro", "email": "test@example.com"}
        r1 = client.post("/billing/checkout", json=payload, headers=headers)
        r2 = client.post("/billing/checkout", json=payload, headers=headers)
        r3 = client.post("/billing/checkout", json=payload, headers=headers)
        r4 = client.post("/billing/checkout", json=payload, headers=headers)

        assert r1.status_code in (200, 400)
        assert r2.status_code in (200, 400)
        assert r3.status_code in (200, 400)
        assert r4.status_code == 429
        assert r4.json()["detail"] == "Too many requests"
        # The middleware sets Retry-After from the limiter (not the raw window).
        retry_after = int(r4.headers.get("retry-after", "0"))
        assert 1 <= retry_after <= 61

    app_with_small_rate_limit.dependency_overrides.clear()


# ----------------------------------------------------------------------
# MemoryRateLimiter
# ----------------------------------------------------------------------
class TestMemoryRateLimiter:

    def test_allows_under_limit(self):
        limiter = rl_mod.MemoryRateLimiter(window_sec=60, max_requests=3)
        for _ in range(3):
            allowed, retry = limiter.check("a")
            assert allowed is True
            assert retry == 0

    def test_blocks_at_limit(self):
        limiter = rl_mod.MemoryRateLimiter(window_sec=60, max_requests=2)
        assert limiter.check("a") == (True, 0)
        assert limiter.check("a") == (True, 0)
        allowed, retry = limiter.check("a")
        assert allowed is False
        assert retry >= 1

    def test_keys_are_independent(self):
        limiter = rl_mod.MemoryRateLimiter(window_sec=60, max_requests=1)
        assert limiter.check("a") == (True, 0)
        # a is over the limit but b is fresh.
        assert limiter.check("a")[0] is False
        assert limiter.check("b") == (True, 0)

    def test_window_release_uses_monotonic_clock(self):
        """Old entries fall out of the window once they age past window_sec."""
        limiter = rl_mod.MemoryRateLimiter(window_sec=1, max_requests=1)
        assert limiter.check("a") == (True, 0)
        assert limiter.check("a")[0] is False
        # Sleep just past the window.
        time.sleep(1.05)
        assert limiter.check("a")[0] is True

    def test_gc_purges_stale_keys(self):
        limiter = rl_mod.MemoryRateLimiter(window_sec=1, max_requests=10)
        # Touch 50 keys, then advance "time" by lying about the GC clock.
        for i in range(50):
            limiter.check(f"k{i}")
        assert len(limiter._buckets) == 50

        # Pretend the last GC was 1 hour ago + every key was last seen ages ago.
        limiter._last_gc = time.monotonic() - 3600
        for k in list(limiter._last_seen):
            limiter._last_seen[k] = time.monotonic() - 3600
        # Trigger GC via a fresh check.
        limiter.check("fresh")
        assert "fresh" in limiter._buckets
        # Every stale key should be gone.
        assert len(limiter._buckets) == 1


# ----------------------------------------------------------------------
# RedisRateLimiter
# ----------------------------------------------------------------------
class TestRedisRateLimiter:

    def _make_limiter(self, script_responses):
        """Build a RedisRateLimiter with a fake redis client + script."""
        with patch.dict("sys.modules", {"redis": MagicMock()}) as mods:
            redis_mod = mods["redis"]
            script_callable = MagicMock(side_effect=script_responses)
            client_mock = MagicMock()
            client_mock.register_script.return_value = script_callable
            redis_mod.Redis.from_url.return_value = client_mock

            limiter = rl_mod.RedisRateLimiter(
                redis_url="redis://localhost:6379/0",
                window_sec=60,
                max_requests=3,
            )
        # Replace the script with the mock so we can drive it directly.
        return limiter, script_callable

    def test_allows_when_script_returns_one(self):
        limiter, script = self._make_limiter([["1", "0"]])
        assert limiter.check("k") == (True, 0)
        script.assert_called_once()

    def test_blocks_when_script_returns_zero(self):
        limiter, _ = self._make_limiter([["0", "5"]])
        assert limiter.check("k") == (False, 5)

    def test_fails_open_when_script_throws(self):
        """If Redis goes down, never break real traffic — allow."""
        limiter, _ = self._make_limiter([RuntimeError("connection reset")])
        allowed, retry = limiter.check("k")
        assert allowed is True
        assert retry == 0


# ----------------------------------------------------------------------
# Factory
# ----------------------------------------------------------------------
class TestGetRateLimiter:

    def setup_method(self):
        rl_mod.reset_for_tests()

    def teardown_method(self):
        rl_mod.reset_for_tests()

    def test_returns_memory_when_no_redis_url(self, monkeypatch):
        monkeypatch.delenv("REDIS_URL", raising=False)
        monkeypatch.setenv("RATE_LIMIT_MAX_REQUESTS", "7")
        limiter = rl_mod.get_rate_limiter()
        assert isinstance(limiter, rl_mod.MemoryRateLimiter)
        assert limiter.max_requests == 7

    def test_returns_redis_when_url_set(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        with patch.dict("sys.modules", {"redis": MagicMock()}) as mods:
            redis_mod = mods["redis"]
            client_mock = MagicMock()
            client_mock.register_script.return_value = MagicMock()
            redis_mod.Redis.from_url.return_value = client_mock

            limiter = rl_mod.get_rate_limiter()
        assert isinstance(limiter, rl_mod.RedisRateLimiter)

    def test_falls_back_to_memory_when_redis_import_fails(self, monkeypatch, capsys):
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        # Force the redis import inside RedisRateLimiter.__init__ to fail.
        with patch.dict("sys.modules", {"redis": None}):
            limiter = rl_mod.get_rate_limiter()
        assert isinstance(limiter, rl_mod.MemoryRateLimiter)
        captured = capsys.readouterr().out
        assert "falling back to memory" in captured

    def test_singleton(self, monkeypatch):
        monkeypatch.delenv("REDIS_URL", raising=False)
        first = rl_mod.get_rate_limiter()
        second = rl_mod.get_rate_limiter()
        assert first is second
