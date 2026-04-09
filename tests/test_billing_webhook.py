"""
Tests for billing checkout and Paystack webhook.
"""

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api.deps import get_current_context, RequestContext


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
    def test_billing_config_returns_paystack(self, client, admin_context):
        app.dependency_overrides[get_current_context] = lambda: admin_context
        try:
            res = client.get("/billing/config")
            assert res.status_code == 200
            data = res.json()
            assert data["provider"] == "paystack"
            assert "paystack" in data
            assert "stripe" not in data
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

    def test_checkout_requires_email(self, client, admin_context):
        app.dependency_overrides[get_current_context] = lambda: admin_context
        try:
            res = client.post("/billing/checkout", json={"plan": "pro"})
            assert res.status_code == 400
            assert "email" in res.json()["detail"].lower()
        finally:
            app.dependency_overrides.clear()

    @patch("app.db.supabase.get_supabase_client")
    @patch("app.clients.paystack.initialize_transaction")
    def test_checkout_paystack_success(self, mock_init, mock_supa, client, admin_context):
        mock_init.return_value = "https://checkout.paystack.com/test"

        supa = MagicMock()
        sub_query = MagicMock()
        sub_query.upsert.return_value = sub_query
        sub_query.execute.return_value = MagicMock(data=None)
        supa.table.return_value = sub_query
        mock_supa.return_value = supa

        app.dependency_overrides[get_current_context] = lambda: admin_context
        try:
            res = client.post("/billing/checkout", json={"plan": "pro", "email": "test@example.com"})
            assert res.status_code == 200
            assert res.json()["url"] == "https://checkout.paystack.com/test"
        finally:
            app.dependency_overrides.clear()


class TestCancel:
    def test_cancel_requires_admin(self, client, cashier_context):
        app.dependency_overrides[get_current_context] = lambda: cashier_context
        try:
            res = client.post("/billing/cancel")
            assert res.status_code == 403
        finally:
            app.dependency_overrides.clear()
