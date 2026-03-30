"""
Tests for Web Push endpoints in app.main.
"""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.deps import get_current_context, RequestContext


@pytest.fixture
def user_context():
    return RequestContext(user_id=str(uuid4()), store_id=str(uuid4()), role="admin")


@pytest.fixture
def client():
    return TestClient(app)


class TestPushVapidKey:
    @patch("app.main.get_vapid_public_key")
    def test_vapid_key_503_when_missing(self, mock_key, client, user_context):
        mock_key.return_value = None
        app.dependency_overrides[get_current_context] = lambda: user_context
        try:
            res = client.get("/push/vapid-public-key")
            assert res.status_code == 503
        finally:
            app.dependency_overrides.clear()

    @patch("app.main.get_vapid_public_key")
    def test_vapid_key_200_when_present(self, mock_key, client, user_context):
        mock_key.return_value = "PUBLICKEY"
        app.dependency_overrides[get_current_context] = lambda: user_context
        try:
            res = client.get("/push/vapid-public-key")
            assert res.status_code == 200
            assert res.json()["public_key"] == "PUBLICKEY"
        finally:
            app.dependency_overrides.clear()


class TestPushSubscribe:
    @patch("app.main.get_supabase_client")
    def test_subscribe_upserts_row(self, mock_supa, client, user_context):
        supa = MagicMock()
        q = MagicMock()
        q.upsert.return_value = q
        q.execute.return_value = MagicMock(data=[{}])
        supa.table.return_value = q
        mock_supa.return_value = supa

        app.dependency_overrides[get_current_context] = lambda: user_context
        try:
            res = client.post("/push/subscribe", json={
                "endpoint": "https://example.com/ep",
                "keys": {"p256dh": "p", "auth": "a"},
            }, headers={"user-agent": "pytest"})
            assert res.status_code == 200
            assert res.json()["success"] is True
            assert q.upsert.called
        finally:
            app.dependency_overrides.clear()


class TestPushTest:
    @patch("app.main.send_web_push")
    @patch("app.main.get_supabase_client")
    def test_push_test_returns_no_subs(self, mock_supa, mock_send, client, user_context):
        supa = MagicMock()
        q = MagicMock()
        q.select.return_value = q
        q.eq.return_value = q
        q.execute.return_value = MagicMock(data=[])
        supa.table.return_value = q
        mock_supa.return_value = supa

        app.dependency_overrides[get_current_context] = lambda: user_context
        try:
            res = client.post("/push/test")
            assert res.status_code == 200
            assert res.json()["sent"] == 0
            mock_send.assert_not_called()
        finally:
            app.dependency_overrides.clear()

    @patch("app.main.send_web_push")
    @patch("app.main.get_supabase_client")
    def test_push_test_sends_when_subs_exist(self, mock_supa, mock_send, client, user_context):
        supa = MagicMock()
        q = MagicMock()
        q.select.return_value = q
        q.eq.return_value = q
        q.execute.return_value = MagicMock(data=[{"endpoint": "e", "p256dh": "p", "auth": "a"}])
        supa.table.return_value = q
        mock_supa.return_value = supa

        mock_send.return_value = {"sent": 1, "failed": 0, "errors": []}

        app.dependency_overrides[get_current_context] = lambda: user_context
        try:
            res = client.post("/push/test")
            assert res.status_code == 200
            assert res.json()["sent"] == 1
            mock_send.assert_called_once()
        finally:
            app.dependency_overrides.clear()

