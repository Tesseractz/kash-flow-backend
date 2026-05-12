"""Tests for FCM (mobile push) wiring.

Covers:
  - app/services/fcm.py: is_fcm_configured, send_fcm with mocked
    firebase-admin (sent, failed, invalid-token detection,
    purge_invalid_tokens cleanup helper).
  - app/services/device_delivery.py: notify_store fans out to BOTH
    Web Push subscriptions AND FCM tokens, and never double-counts.
  - HTTP endpoints: /push/fcm/status, /push/fcm/subscribe,
    /push/fcm/unsubscribe, /push/fcm/test.
"""
import os
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.services.fcm as fcm_mod
import app.services.device_delivery as device_delivery
from app.main import app
from app.api.deps import RequestContext, get_current_context


@pytest.fixture(autouse=True)
def reset_fcm_state():
    fcm_mod.reset_for_tests()
    yield
    fcm_mod.reset_for_tests()


@pytest.fixture
def admin_context():
    return RequestContext(user_id=str(uuid4()), store_id=str(uuid4()), role="admin")


@pytest.fixture
def client():
    return TestClient(app)


# ----------------------------------------------------------------------
# is_fcm_configured
# ----------------------------------------------------------------------
class TestIsConfigured:

    def test_false_when_no_env(self):
        original = {
            "FIREBASE_SERVICE_ACCOUNT_JSON": os.environ.pop("FIREBASE_SERVICE_ACCOUNT_JSON", None),
            "FIREBASE_SERVICE_ACCOUNT_JSON_PATH": os.environ.pop("FIREBASE_SERVICE_ACCOUNT_JSON_PATH", None),
        }
        try:
            assert fcm_mod.is_fcm_configured() is False
        finally:
            for k, v in original.items():
                if v is not None:
                    os.environ[k] = v

    def test_true_with_inline_json(self):
        os.environ["FIREBASE_SERVICE_ACCOUNT_JSON"] = "{}"
        try:
            assert fcm_mod.is_fcm_configured() is True
        finally:
            os.environ.pop("FIREBASE_SERVICE_ACCOUNT_JSON", None)

    def test_true_with_path(self):
        os.environ["FIREBASE_SERVICE_ACCOUNT_JSON_PATH"] = "/etc/firebase.json"
        try:
            assert fcm_mod.is_fcm_configured() is True
        finally:
            os.environ.pop("FIREBASE_SERVICE_ACCOUNT_JSON_PATH", None)


# ----------------------------------------------------------------------
# send_fcm
# ----------------------------------------------------------------------
class TestSendFcm:

    def test_empty_token_list_short_circuits(self):
        result = fcm_mod.send_fcm([], title="x", body="y")
        assert result == {"sent": 0, "failed": 0, "invalid_tokens": [], "errors": []}

    def test_unconfigured_reports_failure_without_calling_firebase(self):
        os.environ.pop("FIREBASE_SERVICE_ACCOUNT_JSON", None)
        os.environ.pop("FIREBASE_SERVICE_ACCOUNT_JSON_PATH", None)
        result = fcm_mod.send_fcm(["tok-1", "tok-2"], title="x", body="y")
        assert result["sent"] == 0
        assert result["failed"] == 2
        assert result["invalid_tokens"] == []
        # The exact wording is "FIREBASE_SERVICE_ACCOUNT_JSON not set" but the
        # contract is "operator-readable string mentioning FCM/Firebase config".
        joined = " ".join(result["errors"]).lower()
        assert "firebase" in joined or "fcm" in joined or "not configured" in joined

    @patch("app.services.fcm.get_fcm_app")
    def test_send_each_for_multicast_path_counts_success_and_invalid(self, mock_get_app):
        mock_get_app.return_value = MagicMock()  # non-None app

        ok_resp = MagicMock(success=True, exception=None)
        unregistered_exc = MagicMock(code="UNREGISTERED")
        unregistered_exc.__str__ = lambda self: "Requested entity was not found"
        unregistered_resp = MagicMock(success=False, exception=unregistered_exc)
        other_exc = MagicMock(code="UNKNOWN")
        other_exc.__str__ = lambda self: "transient server error"
        other_resp = MagicMock(success=False, exception=other_exc)

        fake_messaging = MagicMock()
        fake_messaging.send_each = MagicMock()  # presence of attr triggers multicast path
        fake_messaging.send_each_for_multicast.return_value = MagicMock(
            responses=[ok_resp, unregistered_resp, other_resp]
        )

        with patch.dict("sys.modules", {"firebase_admin": MagicMock(messaging=fake_messaging)}):
            with patch("firebase_admin.messaging", fake_messaging, create=True):
                result = fcm_mod.send_fcm(
                    ["tok-good", "tok-dead", "tok-flaky"],
                    title="t", body="b", url="/x",
                )

        assert result["sent"] == 1
        assert result["failed"] == 2
        assert "tok-dead" in result["invalid_tokens"]
        assert "tok-flaky" not in result["invalid_tokens"]
        assert len(result["errors"]) == 2

    @patch("app.services.fcm.get_fcm_app")
    def test_top_level_exception_is_captured(self, mock_get_app):
        mock_get_app.return_value = MagicMock()
        fake_messaging = MagicMock()
        fake_messaging.send_each = MagicMock()
        fake_messaging.send_each_for_multicast.side_effect = RuntimeError("network down")

        with patch("firebase_admin.messaging", fake_messaging, create=True):
            result = fcm_mod.send_fcm(["t1", "t2"], title="t", body="b")
        assert result["sent"] == 0
        assert result["failed"] == 2
        assert any("network down" in e for e in result["errors"])


# ----------------------------------------------------------------------
# purge_invalid_tokens
# ----------------------------------------------------------------------
class TestPurgeInvalidTokens:

    def test_deletes_each_token_row(self):
        supa = MagicMock()
        n = fcm_mod.purge_invalid_tokens(supa, ["a", "b", "c"])
        assert n == 3
        assert supa.table.return_value.delete.return_value.eq.call_count == 3

    def test_skips_empty_strings(self):
        supa = MagicMock()
        n = fcm_mod.purge_invalid_tokens(supa, ["", None, "real"])
        assert n == 1


# ----------------------------------------------------------------------
# device_delivery.notify_store
# ----------------------------------------------------------------------
class TestNotifyStore:

    @patch("app.services.device_delivery.fcm_mod.send_fcm")
    @patch("app.services.device_delivery.push_mod.send_web_push")
    def test_fans_out_to_both_channels(self, mock_web, mock_fcm):
        supa = MagicMock()
        # First .execute() = push_subscriptions, second = fcm_tokens
        supa.table.return_value.select.return_value.eq.return_value.execute.side_effect = [
            MagicMock(data=[{"endpoint": "https://example", "p256dh": "p", "auth": "a"}]),
            MagicMock(data=[{"token": "fcm-tok-1"}, {"token": "fcm-tok-2"}]),
        ]
        mock_web.return_value = {"sent": 1, "failed": 0, "errors": []}
        mock_fcm.return_value = {"sent": 2, "failed": 0, "invalid_tokens": [], "errors": []}

        result = device_delivery.notify_store(
            supa, "store-1", title="Low stock", body="Only 3 left", url="/products",
        )

        assert result["sent"] == 3
        mock_web.assert_called_once()
        mock_fcm.assert_called_once_with(
            ["fcm-tok-1", "fcm-tok-2"], title="Low stock", body="Only 3 left", url="/products",
        )

    @patch("app.services.device_delivery.fcm_mod.send_fcm")
    @patch("app.services.device_delivery.push_mod.send_web_push")
    def test_skips_channels_with_no_recipients(self, mock_web, mock_fcm):
        supa = MagicMock()
        supa.table.return_value.select.return_value.eq.return_value.execute.side_effect = [
            MagicMock(data=[]),
            MagicMock(data=[]),
        ]

        result = device_delivery.notify_store(supa, "store-1", title="x", body="y")

        assert result["sent"] == 0
        mock_web.assert_not_called()
        mock_fcm.assert_not_called()

    @patch("app.services.device_delivery.fcm_mod.purge_invalid_tokens")
    @patch("app.services.device_delivery.fcm_mod.send_fcm")
    @patch("app.services.device_delivery.push_mod.send_web_push")
    def test_purges_invalid_tokens_after_send(self, _web, mock_fcm, mock_purge):
        supa = MagicMock()
        supa.table.return_value.select.return_value.eq.return_value.execute.side_effect = [
            MagicMock(data=[]),
            MagicMock(data=[{"token": "stale-tok"}]),
        ]
        mock_fcm.return_value = {
            "sent": 0, "failed": 1, "invalid_tokens": ["stale-tok"], "errors": ["UNREGISTERED"],
        }

        device_delivery.notify_store(supa, "store-1", title="x", body="y")

        mock_purge.assert_called_once_with(supa, ["stale-tok"])


# ----------------------------------------------------------------------
# HTTP endpoints
# ----------------------------------------------------------------------
class TestFcmEndpoints:

    @patch("app.api.routers.notifications.fcm_mod.is_fcm_configured", return_value=True)
    def test_status_reports_configured(self, _mock, client, admin_context):
        app.dependency_overrides[get_current_context] = lambda: admin_context
        try:
            resp = client.get("/push/fcm/status")
            assert resp.status_code == 200
            assert resp.json() == {"configured": True}
        finally:
            app.dependency_overrides.clear()

    @patch("app.db.supabase.get_supabase_client")
    def test_subscribe_rejects_short_token(self, _supa, client, admin_context):
        app.dependency_overrides[get_current_context] = lambda: admin_context
        try:
            resp = client.post(
                "/push/fcm/subscribe",
                json={"token": "abc", "platform": "android"},
            )
            assert resp.status_code == 400
            assert "Invalid FCM token" in resp.json()["detail"]
        finally:
            app.dependency_overrides.clear()

    @patch("app.db.supabase.get_supabase_client")
    def test_subscribe_upserts_on_token(self, mock_supa, client, admin_context):
        supa = MagicMock()
        mock_supa.return_value = supa

        app.dependency_overrides[get_current_context] = lambda: admin_context
        try:
            resp = client.post(
                "/push/fcm/subscribe",
                json={
                    "token": "a" * 32,
                    "platform": "android",
                    "device_info": "Pixel 6",
                },
            )
            assert resp.status_code == 200
            assert resp.json()["success"] is True
            supa.table.assert_any_call("fcm_tokens")
            upsert_args = supa.table.return_value.upsert.call_args
            row = upsert_args.args[0]
            assert row["token"] == "a" * 32
            assert row["platform"] == "android"
            assert row["device_info"] == "Pixel 6"
            assert upsert_args.kwargs.get("on_conflict") == "token"
        finally:
            app.dependency_overrides.clear()

    @patch("app.db.supabase.get_supabase_client")
    def test_unsubscribe_deletes_row(self, mock_supa, client, admin_context):
        supa = MagicMock()
        mock_supa.return_value = supa

        app.dependency_overrides[get_current_context] = lambda: admin_context
        try:
            resp = client.post(
                "/push/fcm/unsubscribe",
                json={"token": "fcm-tok-xyz"},
            )
            assert resp.status_code == 200
            supa.table.return_value.delete.return_value.eq.return_value.eq.return_value.execute.assert_called_once()
        finally:
            app.dependency_overrides.clear()

    @patch("app.api.routers.notifications.fcm_mod.is_fcm_configured", return_value=False)
    def test_test_endpoint_503_when_unconfigured(self, _mock, client, admin_context):
        app.dependency_overrides[get_current_context] = lambda: admin_context
        try:
            resp = client.post("/push/fcm/test")
            assert resp.status_code == 503
        finally:
            app.dependency_overrides.clear()

    @patch("app.api.routers.notifications.fcm_mod.send_fcm")
    @patch("app.api.routers.notifications.fcm_mod.is_fcm_configured", return_value=True)
    @patch("app.db.supabase.get_supabase_client")
    def test_test_endpoint_sends_to_user_tokens(self, mock_supa, _cfg, mock_send, client, admin_context):
        supa = MagicMock()
        mock_supa.return_value = supa
        supa.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"token": "tok-1"}, {"token": "tok-2"}]
        )
        mock_send.return_value = {"sent": 2, "failed": 0, "invalid_tokens": [], "errors": []}

        app.dependency_overrides[get_current_context] = lambda: admin_context
        try:
            resp = client.post("/push/fcm/test")
            assert resp.status_code == 200
            assert resp.json()["sent"] == 2
            mock_send.assert_called_once_with(
                ["tok-1", "tok-2"], title="KashPoint test", body="Native push is working.", url="/",
            )
        finally:
            app.dependency_overrides.clear()
