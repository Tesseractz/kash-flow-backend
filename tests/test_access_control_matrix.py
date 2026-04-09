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
    from app.api.deps import get_current_context, RequestContext

    app.dependency_overrides[get_current_context] = lambda: RequestContext(
        user_id="u_admin", store_id="s1", role="admin"
    )
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture
def cashier_client():
    from app.main import app
    from app.api.deps import get_current_context, RequestContext

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
    ("POST", "/billing/cancel"),
    ("POST", "/billing/paystack/sync"),
    ("GET", "/audit-logs"),
]


@pytest.mark.parametrize("method,path", SENSITIVE_ADMIN_ONLY)
def test_cashier_forbidden_for_sensitive_endpoints(cashier_client, method, path):
    if method == "POST" and path == "/billing/checkout":
        res = cashier_client.request(method, path, json={"plan": "pro"})
    elif method == "POST" and path == "/billing/paystack/sync":
        res = cashier_client.request(method, path, json={"reference": "ref_test"})
    else:
        res = cashier_client.request(method, path)
    assert res.status_code == 403
