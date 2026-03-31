"""
Access-control matrix tests (admin vs cashier) for sensitive endpoints.
These tests are high-signal for preventing privilege escalation and IDOR issues.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client():
    from app.main import app
    from app.deps import get_current_context, RequestContext

    app.dependency_overrides[get_current_context] = lambda: RequestContext(
        user_id="u_admin", store_id="s1", role="admin"
    )
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture
def cashier_client():
    from app.main import app
    from app.deps import get_current_context, RequestContext

    app.dependency_overrides[get_current_context] = lambda: RequestContext(
        user_id="u_cashier", store_id="s1", role="cashier"
    )
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


SENSITIVE_ADMIN_ONLY = [
    ("GET", "/reports"),
    ("GET", "/reports/export"),
    ("POST", "/billing/checkout"),
    ("POST", "/billing/portal"),
    ("GET", "/audit-logs"),
]


@pytest.mark.parametrize("method,path", SENSITIVE_ADMIN_ONLY)
def test_cashier_forbidden_for_sensitive_endpoints(cashier_client, method, path):
    if method == "POST" and path == "/billing/checkout":
        res = cashier_client.request(method, path, json={"plan": "pro"})
    else:
        res = cashier_client.request(method, path)
    assert res.status_code == 403


def test_cashier_cannot_create_customer_portal(cashier_client):
    res = cashier_client.post("/billing/portal")
    assert res.status_code == 403


@patch("app.main.get_supabase_client")
@patch("app.main.get_stripe_client")
def test_admin_can_create_portal_when_customer_exists(mock_stripe, mock_supa, admin_client, sample_subscription_pro):
    import os
    original_provider = os.environ.get("BILLING_PROVIDER")
    os.environ["BILLING_PROVIDER"] = "stripe"
    stripe = MagicMock()
    stripe.billing_portal.Session.create.return_value = {"url": "https://portal.test/session"}
    mock_stripe.return_value = stripe

    supa = MagicMock()
    q = MagicMock()
    q.select.return_value = q
    q.eq.return_value = q
    q.single.return_value = q
    q.execute.return_value = MagicMock(data=sample_subscription_pro)
    supa.table.return_value = q
    mock_supa.return_value = supa

    try:
        res = admin_client.post("/billing/portal")
        assert res.status_code == 200
        assert "url" in res.json()
    finally:
        if original_provider is not None:
            os.environ["BILLING_PROVIDER"] = original_provider
        else:
            os.environ.pop("BILLING_PROVIDER", None)

