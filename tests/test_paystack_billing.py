import json
import hmac
import hashlib
from unittest.mock import MagicMock, patch

import pytest

from app.main import app
from app.deps import get_current_context, RequestContext


@pytest.fixture
def admin_ctx():
    return RequestContext(user_id="u1", store_id="store_1", role="admin")


@pytest.fixture
def client(admin_ctx):
    from fastapi.testclient import TestClient

    app.dependency_overrides[get_current_context] = lambda: admin_ctx
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@patch("app.main.get_supabase_client")
@patch("app.main.initialize_transaction")
def test_paystack_checkout_requires_email(mock_init_tx, mock_supa, client):
    mock_supa.return_value = MagicMock()
    resp = client.post("/billing/checkout", json={"plan": "pro"})
    assert resp.status_code == 400
    assert "email" in resp.json()["detail"].lower()


@patch("app.main.get_supabase_client")
@patch("app.main.initialize_transaction")
def test_paystack_checkout_returns_authorization_url(mock_init_tx, mock_supa, client, monkeypatch):
    monkeypatch.setenv("BILLING_PROVIDER", "paystack")
    mock_supa.return_value = MagicMock()
    mock_init_tx.return_value = "https://checkout.paystack.com/abc"

    resp = client.post("/billing/checkout", json={"plan": "pro", "email": "a@b.com"})
    assert resp.status_code == 200
    assert resp.json()["url"].startswith("https://")
    mock_init_tx.assert_called_once()


def _sign(secret: str, raw: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), raw, hashlib.sha512).hexdigest()


@patch("app.main.get_supabase_client")
def test_paystack_webhook_rejects_bad_signature(mock_supa, client, monkeypatch):
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_x")
    mock_supa.return_value = MagicMock()
    payload = {"event": "charge.success", "data": {"id": 1}}
    raw = json.dumps(payload).encode("utf-8")

    resp = client.post("/paystack/webhook", content=raw, headers={"x-paystack-signature": "bad"})
    assert resp.status_code == 400


@patch("app.main.get_supabase_client")
def test_paystack_webhook_charge_success_upserts_subscription(mock_supa, client, monkeypatch):
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_x")
    mock_client = MagicMock()
    mock_supa.return_value = mock_client

    payload = {
        "event": "charge.success",
        "data": {
            "id": 123,
            "metadata": {"store_id": "store_1", "plan": "pro"},
            "customer": {"customer_code": "CUS_x"},
            "subscription": {"subscription_code": "SUB_x", "email_token": "EMT_x"},
        },
    }
    raw = json.dumps(payload).encode("utf-8")
    sig = _sign("sk_test_x", raw)

    resp = client.post("/paystack/webhook", content=raw, headers={"x-paystack-signature": sig})
    assert resp.status_code == 200

    # Dedup insert + upsert should happen
    assert mock_client.table.call_count >= 1
    # Ensure subscriptions upsert called with active status
    subs_table = mock_client.table.return_value
    upsert_calls = [c for c in subs_table.upsert.call_args_list]
    assert any((c.args and c.args[0].get("status") == "active") for c in upsert_calls)

