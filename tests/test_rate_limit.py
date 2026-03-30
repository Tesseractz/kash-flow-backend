import importlib
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_with_small_rate_limit(monkeypatch):
    """
    Reload app.main after setting env so module-level rate limit config is applied.
    """
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SEC", "60")
    monkeypatch.setenv("RATE_LIMIT_MAX_REQUESTS", "3")

    import app.main as main_mod
    importlib.reload(main_mod)
    return main_mod.app


def test_rate_limit_blocks_after_threshold(app_with_small_rate_limit, monkeypatch):
    """
    Hit /billing/portal 4 times from same IP; the 4th should be 429.
    """
    from app.deps import RequestContext
    from app.deps import get_current_context

    client = TestClient(app_with_small_rate_limit)

    # Override auth to avoid 401 and focus on limiter
    app_with_small_rate_limit.dependency_overrides[get_current_context] = lambda: RequestContext(
        user_id="u1", store_id="s1", role="admin"
    )

    # Mock supabase/stripe inside endpoint to prevent real calls
    with patch("app.main.get_supabase_client") as mock_supa, patch("app.main.get_stripe_client") as mock_stripe:
        mock_supa.return_value = MagicMock()
        stripe = MagicMock()
        stripe.billing_portal.Session.create.return_value = {"url": "https://portal.test/session"}
        mock_stripe.return_value = stripe

        headers = {"x-forwarded-for": "1.2.3.4"}
        r1 = client.post("/billing/portal", headers=headers)
        r2 = client.post("/billing/portal", headers=headers)
        r3 = client.post("/billing/portal", headers=headers)
        r4 = client.post("/billing/portal", headers=headers)

        assert r1.status_code in (200, 400)  # depends on mocked supabase shape; limiter should allow
        assert r2.status_code in (200, 400)
        assert r3.status_code in (200, 400)
        assert r4.status_code == 429
        assert r4.json()["detail"] == "Too many requests"

    app_with_small_rate_limit.dependency_overrides.clear()

