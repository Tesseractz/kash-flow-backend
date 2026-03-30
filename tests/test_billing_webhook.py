"""
Tests for billing checkout/portal and Stripe webhook hardening.
"""

import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.deps import get_current_context, RequestContext


@pytest.fixture
def admin_context():
    return RequestContext(user_id="user-1", store_id="store-1", role="admin")


@pytest.fixture
def cashier_context():
    return RequestContext(user_id="user-2", store_id="store-2", role="cashier")


@pytest.fixture
def client():
    return TestClient(app)


class TestBillingConfig:
    def test_billing_config_only_returns_pro(self, client, admin_context):
        app.dependency_overrides[get_current_context] = lambda: admin_context
        try:
            res = client.get("/billing/config")
            assert res.status_code == 200
            data = res.json()
            assert "prices" in data
            assert "pro" in data["prices"]
            assert "business" not in data["prices"]
        finally:
            app.dependency_overrides.clear()


class TestCheckout:
    def test_checkout_requires_admin(self, client, cashier_context):
        app.dependency_overrides[get_current_context] = lambda: cashier_context
        try:
            res = client.post("/billing/checkout", json={"plan": "pro"})
            assert res.status_code == 403
        finally:
            app.dependency_overrides.clear()

    def test_checkout_rejects_non_pro_plan(self, client, admin_context):
        """Schema should block legacy 'business' from being accepted."""
        app.dependency_overrides[get_current_context] = lambda: admin_context
        try:
            res = client.post("/billing/checkout", json={"plan": "business"})
            assert res.status_code == 422
        finally:
            app.dependency_overrides.clear()

    @patch("app.main.get_supabase_client")
    @patch("app.main.get_stripe_client")
    def test_checkout_uses_pro_price_and_safe_redirects(self, mock_stripe, mock_supa, client, admin_context, monkeypatch):
        monkeypatch.setenv("STRIPE_PRO_PRICE_ID", "price_pro_123")
        monkeypatch.setenv("FRONTEND_URL", "http://localhost:5001/")  # trailing slash should not cause //

        stripe = MagicMock()
        stripe.Customer.create.return_value = {"id": "cus_123"}
        stripe.checkout.Session.create.return_value = {"url": "https://checkout.test/session"}
        mock_stripe.return_value = stripe

        supa = MagicMock()
        # No existing subscription row
        sub_query = MagicMock()
        sub_query.select.return_value = sub_query
        sub_query.eq.return_value = sub_query
        sub_query.single.return_value = sub_query
        sub_query.execute.return_value = MagicMock(data=None)
        supa.table.return_value = sub_query
        mock_supa.return_value = supa

        app.dependency_overrides[get_current_context] = lambda: admin_context
        try:
            res = client.post("/billing/checkout", json={"plan": "pro"})
            assert res.status_code == 200
            assert res.json()["url"] == "https://checkout.test/session"

            kwargs = stripe.checkout.Session.create.call_args.kwargs
            assert kwargs["mode"] == "subscription"
            assert kwargs["line_items"] == [{"price": "price_pro_123", "quantity": 1}]
            assert kwargs["success_url"] == "http://localhost:5001/billing?success=1"
            assert kwargs["cancel_url"] == "http://localhost:5001/billing?canceled=1"
            assert kwargs["payment_method_collection"] == "always"
            assert kwargs["subscription_data"]["trial_period_days"] == 30
            assert kwargs["metadata"]["plan"] == "pro"
            assert kwargs["metadata"]["store_id"] == "store-1"
        finally:
            app.dependency_overrides.clear()

    def test_checkout_returns_400_when_price_missing(self, client, admin_context, monkeypatch):
        monkeypatch.setenv("STRIPE_PRO_PRICE_ID", "")
        app.dependency_overrides[get_current_context] = lambda: admin_context
        try:
            with patch("app.main.get_supabase_client") as mock_supa, patch("app.main.get_stripe_client") as mock_stripe:
                # prevent real Supabase/Stripe init
                mock_supa.return_value = MagicMock()
                mock_stripe.return_value = MagicMock()
                res = client.post("/billing/checkout", json={"plan": "pro"})
            assert res.status_code == 400
            assert "STRIPE_PRO_PRICE_ID" in res.json()["detail"]
        finally:
            app.dependency_overrides.clear()


class TestPortal:
    def test_portal_requires_admin(self, client, cashier_context):
        app.dependency_overrides[get_current_context] = lambda: cashier_context
        try:
            res = client.post("/billing/portal")
            assert res.status_code == 403
        finally:
            app.dependency_overrides.clear()


class TestStripeWebhook:
    @patch("app.main.get_stripe_client")
    def test_webhook_missing_secret(self, mock_stripe, client, monkeypatch):
        monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
        res = client.post("/stripe/webhook", data=b"{}", headers={"stripe-signature": "t=1,v1=x"})
        assert res.status_code == 500

    @patch("app.main.get_stripe_client")
    def test_webhook_missing_signature_header(self, mock_stripe, client, monkeypatch):
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
        res = client.post("/stripe/webhook", data=b"{}")
        assert res.status_code == 400

    @patch("app.main.get_stripe_client")
    def test_webhook_invalid_signature(self, mock_stripe, client, monkeypatch):
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
        stripe = MagicMock()
        stripe.Webhook.construct_event.side_effect = Exception("bad sig")
        mock_stripe.return_value = stripe

        res = client.post("/stripe/webhook", data=b"{}", headers={"stripe-signature": "bad"})
        assert res.status_code == 400

    @patch("app.main.get_supabase_client")
    @patch("app.main.get_stripe_client")
    def test_webhook_normalizes_business_plan_to_pro(self, mock_stripe, mock_supa, client, monkeypatch):
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")

        event = {
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_123",
                    "status": "active",
                    "customer": "cus_123",
                    "metadata": {"store_id": "store-1", "plan": "business"},
                    "current_period_end": int(datetime.now(timezone.utc).timestamp()),
                }
            },
        }

        stripe = MagicMock()
        stripe.Webhook.construct_event.return_value = event
        mock_stripe.return_value = stripe

        supa = MagicMock()
        table_q = MagicMock()
        upsert_q = MagicMock()
        upsert_q.execute.return_value = MagicMock(data=[{}])
        table_q.upsert.return_value = upsert_q
        supa.table.return_value = table_q
        mock_supa.return_value = supa

        res = client.post("/stripe/webhook", data=b"{}", headers={"stripe-signature": "t=1,v1=x"})
        assert res.status_code == 200

        # Ensure 'plan' sent to upsert is 'pro'
        upsert_args = table_q.upsert.call_args.args[0]
        assert upsert_args["plan"] == "pro"

    @patch("app.main.get_supabase_client")
    @patch("app.main.get_stripe_client")
    def test_webhook_subscription_deleted_sets_expired(self, mock_stripe, mock_supa, client, monkeypatch):
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")

        event = {
            "type": "customer.subscription.deleted",
            "data": {"object": {"id": "sub_123", "customer": "cus_123", "metadata": {"store_id": "store-1"}}},
        }
        stripe = MagicMock()
        stripe.Webhook.construct_event.return_value = event
        mock_stripe.return_value = stripe

        supa = MagicMock()
        table_q = MagicMock()
        upsert_q = MagicMock()
        upsert_q.execute.return_value = MagicMock(data=[{}])
        table_q.upsert.return_value = upsert_q
        supa.table.return_value = table_q
        mock_supa.return_value = supa

        res = client.post("/stripe/webhook", data=b"{}", headers={"stripe-signature": "t=1,v1=x"})
        assert res.status_code == 200

        upsert_args = table_q.upsert.call_args.args[0]
        assert upsert_args["store_id"] == "store-1"
        assert upsert_args["status"] == "canceled"
        assert upsert_args["plan"] == "expired"

    @patch("app.main.get_supabase_client")
    @patch("app.main.get_stripe_client")
    def test_webhook_invoice_payment_failed_marks_past_due(self, mock_stripe, mock_supa, client, monkeypatch):
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")

        event = {
            "type": "invoice.payment_failed",
            "data": {"object": {"customer": "cus_123"}},
        }
        stripe = MagicMock()
        stripe.Webhook.construct_event.return_value = event
        mock_stripe.return_value = stripe

        supa = MagicMock()
        table_q = MagicMock()

        # select chain
        table_q.select.return_value = table_q
        table_q.eq.return_value = table_q
        table_q.execute.return_value = MagicMock(data=[{"store_id": "store-1"}])

        # update chain (same table mock; update() will be asserted)
        table_q.update.return_value = table_q

        supa.table.return_value = table_q
        mock_supa.return_value = supa

        res = client.post("/stripe/webhook", data=b"{}", headers={"stripe-signature": "t=1,v1=x"})
        assert res.status_code == 200

        table_q.update.assert_called_with({"status": "past_due"})
        # it should scope by store_id
        assert any(call.args[0] == "store_id" for call in table_q.eq.call_args_list)

