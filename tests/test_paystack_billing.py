import json
import hmac
import hashlib
import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.main import app
from app.api.deps import get_current_context, RequestContext


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


@patch("app.db.supabase.get_supabase_client")
@patch("app.clients.paystack.initialize_transaction")
def test_paystack_checkout_requires_email(mock_init_tx, mock_supa, client):
    mock_supa.return_value = MagicMock()
    resp = client.post("/billing/checkout", json={"plan": "pro"})
    assert resp.status_code == 400
    assert "email" in resp.json()["detail"].lower()


@patch("app.db.supabase.get_supabase_client")
@patch("app.clients.paystack.initialize_transaction")
def test_paystack_checkout_returns_authorization_url(mock_init_tx, mock_supa, client, monkeypatch):
    monkeypatch.setenv("BILLING_PROVIDER", "paystack")
    mock_supa.return_value = MagicMock()
    mock_init_tx.return_value = "https://checkout.paystack.com/abc"

    resp = client.post("/billing/checkout", json={"plan": "pro", "email": "a@b.com"})
    assert resp.status_code == 200
    assert resp.json()["url"].startswith("https://")
    mock_init_tx.assert_called_once()


@patch("app.core.http_config.allowed_origins", ["*"])
@patch("app.db.supabase.get_supabase_client")
@patch("app.clients.paystack.initialize_transaction")
def test_paystack_checkout_callback_prefers_browser_origin(mock_init_tx, mock_supa, client, monkeypatch):
    """When FRONTEND_URL is wrong, Origin from the SPA should set Paystack callback_url."""
    monkeypatch.setenv("BILLING_PROVIDER", "paystack")
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:5001")
    mock_supa.return_value = MagicMock()
    mock_init_tx.return_value = "https://checkout.paystack.com/abc"

    resp = client.post(
        "/billing/checkout",
        json={"plan": "pro", "email": "a@b.com"},
        headers={"Origin": "http://localhost:5000"},
    )
    assert resp.status_code == 200
    kwargs = mock_init_tx.call_args.kwargs
    assert "http://localhost:5000" in kwargs["callback_url"]
    assert "5001" not in kwargs["callback_url"]


@patch("app.core.http_config.allowed_origins", ["http://localhost:5001"])
@patch("app.db.supabase.get_supabase_client")
@patch("app.clients.paystack.initialize_transaction")
def test_paystack_checkout_callback_uses_spa_port_when_cors_lists_other_loopback_port(
    mock_init_tx, mock_supa, client, monkeypatch
):
    """Explicit CORS for :5001 must not force Paystack redirect to :5001 when the SPA is on :5000."""
    monkeypatch.setenv("BILLING_PROVIDER", "paystack")
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:5001")
    mock_supa.return_value = MagicMock()
    mock_init_tx.return_value = "https://checkout.paystack.com/abc"

    resp = client.post(
        "/billing/checkout",
        json={"plan": "pro", "email": "a@b.com"},
        headers={"X-App-Origin": "http://localhost:5000"},
    )
    assert resp.status_code == 200
    kwargs = mock_init_tx.call_args.kwargs
    assert kwargs["callback_url"].startswith("http://localhost:5000/billing")
    assert "5001" not in kwargs["callback_url"]


def test_get_billing_redirects_to_spa_preserving_paystack_query(client, monkeypatch):
    """If the browser hits the API /billing after Paystack, forward to FRONTEND_URL (Vite)."""
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:5000")
    resp = client.get(
        "/billing?success=1&trxref=ffg6lel07t&reference=ffg6lel07t",
        follow_redirects=False,
    )
    assert resp.status_code == 307
    assert resp.headers["location"] == (
        "http://localhost:5000/billing?success=1&trxref=ffg6lel07t&reference=ffg6lel07t"
    )


def _sign(secret: str, raw: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), raw, hashlib.sha512).hexdigest()


@patch("app.db.supabase.get_supabase_client")
def test_paystack_webhook_rejects_bad_signature(mock_supa, client, monkeypatch):
    monkeypatch.setenv("PAYSTACK_MODE", "test")
    monkeypatch.setenv("PAYSTACK_SECRET_KEY_TEST", "sk_test_x")
    mock_supa.return_value = MagicMock()
    payload = {"event": "charge.success", "data": {"id": 1}}
    raw = json.dumps(payload).encode("utf-8")

    resp = client.post("/paystack/webhook", content=raw, headers={"x-paystack-signature": "bad"})
    assert resp.status_code == 400


@patch("app.db.supabase.get_supabase_client")
def test_paystack_webhook_charge_success_upserts_subscription(mock_supa, client, monkeypatch):
    monkeypatch.setenv("PAYSTACK_MODE", "test")
    monkeypatch.setenv("PAYSTACK_SECRET_KEY_TEST", "sk_test_x")
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
    assert resp.status_code == 200, resp.text

    # Dedup insert + upsert should happen
    assert mock_client.table.call_count >= 1
    # Ensure subscriptions upsert called with active status
    subs_table = mock_client.table.return_value
    upsert_calls = [c for c in subs_table.upsert.call_args_list]
    assert any((c.args and c.args[0].get("status") == "active") for c in upsert_calls)


@patch("app.clients.paystack.verify_transaction")
@patch("app.db.supabase.get_supabase_client")
def test_paystack_sync_persists_subscription_codes(mock_supa, mock_verify, client, monkeypatch):
    monkeypatch.setenv("BILLING_PROVIDER", "paystack")
    monkeypatch.setenv("PAYSTACK_MODE", "test")
    store = str(uuid.uuid4())
    mock_verify.return_value = {
        "status": "success",
        "metadata": {"store_id": store, "plan": "pro"},
        "subscription_code": "SUB_x",
        "email_token": "EMT_x",
        "customer": {"customer_code": "CUS_x"},
    }
    mock_supa.return_value = MagicMock()

    app.dependency_overrides[get_current_context] = lambda: RequestContext(
        user_id="u1", store_id=store, role="admin"
    )
    try:
        resp = client.post("/billing/paystack/sync", json={"reference": "ref_123"})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["synced"] is True
        assert data["has_subscription"] is True
    finally:
        app.dependency_overrides.clear()


@patch("app.clients.paystack.verify_transaction")
def test_paystack_sync_rejects_wrong_store(mock_verify, client, monkeypatch):
    monkeypatch.setenv("BILLING_PROVIDER", "paystack")
    mock_verify.return_value = {
        "status": "success",
        "metadata": {"store_id": str(uuid.uuid4()), "plan": "pro"},
        "subscription_code": "SUB_x",
        "email_token": "EMT_x",
    }
    other_store = str(uuid.uuid4())
    app.dependency_overrides[get_current_context] = lambda: RequestContext(
        user_id="u1", store_id=other_store, role="admin"
    )
    try:
        resp = client.post("/billing/paystack/sync", json={"reference": "ref_123"})
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()

