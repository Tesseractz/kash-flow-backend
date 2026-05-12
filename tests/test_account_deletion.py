"""Tests for the account-deletion service and its endpoints.

Covers the new /privacy/delete-account/execute path (immediate deletion),
the /privacy/scheduled-deletions/process cron endpoint, the password
verification helper, and the data-wipe orchestration.
"""
import os
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api.deps import RequestContext, get_current_context
import app.services.account_deletion as account_deletion


@pytest.fixture
def user_context():
    return RequestContext(
        user_id=str(uuid4()),
        store_id=str(uuid4()),
        role="cashier",
    )


@pytest.fixture
def client():
    return TestClient(app)


# ----------------------------------------------------------------------
# verify_user_password
# ----------------------------------------------------------------------
class TestVerifyUserPassword:

    def test_returns_false_for_empty_password(self):
        """Empty password must fail closed without hitting the network."""
        assert account_deletion.verify_user_password("any-user", "") is False

    @patch("app.services.account_deletion.httpx.Client")
    def test_returns_false_when_email_lookup_fails(self, mock_client_cls):
        """If the admin email lookup 4xx/5xxs, refuse — never bypass."""
        instance = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = instance
        instance.get.return_value = MagicMock(status_code=404)

        assert account_deletion.verify_user_password("u-1", "anything") is False
        instance.post.assert_not_called()

    @patch("app.services.account_deletion.httpx.Client")
    def test_returns_true_when_password_endpoint_200s(self, mock_client_cls):
        instance = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = instance

        get_resp = MagicMock(status_code=200)
        get_resp.json.return_value = {"email": "owner@example.com"}
        post_resp = MagicMock(status_code=200)

        instance.get.return_value = get_resp
        instance.post.return_value = post_resp

        assert account_deletion.verify_user_password("u-1", "correct-pw") is True

    @patch("app.services.account_deletion.httpx.Client")
    def test_returns_false_when_password_endpoint_400s(self, mock_client_cls):
        instance = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = instance

        get_resp = MagicMock(status_code=200)
        get_resp.json.return_value = {"email": "owner@example.com"}
        post_resp = MagicMock(status_code=400)

        instance.get.return_value = get_resp
        instance.post.return_value = post_resp

        assert account_deletion.verify_user_password("u-1", "wrong-pw") is False

    @patch(
        "app.services.account_deletion.httpx.Client",
        side_effect=RuntimeError("network down"),
    )
    def test_returns_false_on_network_exception(self, _mock_client_cls):
        """Network errors must fail closed."""
        assert account_deletion.verify_user_password("u-1", "x") is False


# ----------------------------------------------------------------------
# delete_user_and_owned_data
# ----------------------------------------------------------------------
class TestDeleteUserAndOwnedData:

    @patch("app.services.account_deletion.httpx.Client")
    @patch("app.services.account_deletion.get_supabase_client")
    def test_deletes_store_when_user_is_owner(self, mock_supa, mock_client_cls, user_context):
        supa = MagicMock()
        mock_supa.return_value = supa

        # profile lookup returns the store
        supa.table.return_value.select.return_value.eq.return_value.single.return_value.execute.side_effect = [
            MagicMock(data={"store_id": user_context.store_id}),  # profiles
            MagicMock(data={"owner_id": user_context.user_id}),    # stores
        ]

        # Supabase admin DELETE responds 204
        http = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = http
        http.delete.return_value = MagicMock(status_code=204)

        result = account_deletion.delete_user_and_owned_data(user_context.user_id)

        assert result["auth_user_deleted"] is True
        assert result["store_deleted"] is True
        # Every per-user table got a delete + the store + profile.
        deleted_tables = [
            call.args[0]
            for call in supa.table.call_args_list
        ]
        for required in (
            "push_subscriptions",
            "cookie_preferences",
            "user_consents",
            "user_sessions",
            "data_export_requests",
            "stores",
            "profiles",
            "account_deletion_requests",
        ):
            assert required in deleted_tables, f"missing {required}"

    @patch("app.services.account_deletion.httpx.Client")
    @patch("app.services.account_deletion.get_supabase_client")
    def test_keeps_store_when_user_is_not_owner(self, mock_supa, mock_client_cls, user_context):
        """A cashier deleting their account must NOT take down their employer's store."""
        supa = MagicMock()
        mock_supa.return_value = supa

        other_owner = str(uuid4())
        supa.table.return_value.select.return_value.eq.return_value.single.return_value.execute.side_effect = [
            MagicMock(data={"store_id": user_context.store_id}),
            MagicMock(data={"owner_id": other_owner}),
        ]

        http = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = http
        http.delete.return_value = MagicMock(status_code=204)

        result = account_deletion.delete_user_and_owned_data(user_context.user_id)

        # store_deleted flag must be False — that's the only contract the caller relies on.
        # (We can't easily assert "no delete() on stores" because MagicMock chains share
        # state across `.table("stores").select()` lookup AND `.table("stores").delete()`.)
        assert result["store_deleted"] is False

    @patch("app.services.account_deletion.httpx.Client")
    @patch("app.services.account_deletion.get_supabase_client")
    def test_returns_auth_deleted_false_when_admin_api_fails(self, mock_supa, mock_client_cls, user_context):
        supa = MagicMock()
        mock_supa.return_value = supa
        supa.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(
            data=None,
        )

        http = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = http
        http.delete.return_value = MagicMock(status_code=500, text="boom")

        result = account_deletion.delete_user_and_owned_data(user_context.user_id)
        assert result["auth_user_deleted"] is False


# ----------------------------------------------------------------------
# /privacy/delete-account/execute
# ----------------------------------------------------------------------
class TestExecuteAccountDeletionEndpoint:

    @patch("app.api.routers.privacy.account_deletion.delete_user_and_owned_data")
    @patch("app.api.routers.privacy.account_deletion.verify_user_password", return_value=True)
    @patch("app.services.audit_log.log_audit_event")
    def test_executes_when_password_correct(
        self, _audit, _verify_pw, mock_delete, client, user_context
    ):
        mock_delete.return_value = {
            "auth_user_deleted": True,
            "store_deleted": True,
            "store_id": user_context.store_id,
        }

        app.dependency_overrides[get_current_context] = lambda: user_context
        try:
            resp = client.post(
                "/privacy/delete-account/execute",
                json={"confirm_password": "correct"},
            )
            assert resp.status_code == 200
            assert resp.json() == {"deleted": True, "store_deleted": True}
            mock_delete.assert_called_once_with(user_context.user_id)
        finally:
            app.dependency_overrides.clear()

    @patch("app.api.routers.privacy.account_deletion.delete_user_and_owned_data")
    @patch("app.api.routers.privacy.account_deletion.verify_user_password", return_value=False)
    def test_rejects_when_password_wrong(self, _verify_pw, mock_delete, client, user_context):
        app.dependency_overrides[get_current_context] = lambda: user_context
        try:
            resp = client.post(
                "/privacy/delete-account/execute",
                json={"confirm_password": "nope"},
            )
            assert resp.status_code == 401
            mock_delete.assert_not_called()
        finally:
            app.dependency_overrides.clear()

    @patch("app.api.routers.privacy.account_deletion.delete_user_and_owned_data")
    @patch("app.api.routers.privacy.account_deletion.verify_user_password", return_value=True)
    @patch("app.services.audit_log.log_audit_event")
    def test_returns_500_when_auth_delete_fails(
        self, _audit, _verify_pw, mock_delete, client, user_context
    ):
        mock_delete.return_value = {"auth_user_deleted": False, "store_deleted": False}

        app.dependency_overrides[get_current_context] = lambda: user_context
        try:
            resp = client.post(
                "/privacy/delete-account/execute",
                json={"confirm_password": "correct"},
            )
            assert resp.status_code == 500
            assert "support" in resp.json()["detail"].lower()
        finally:
            app.dependency_overrides.clear()


# ----------------------------------------------------------------------
# /privacy/scheduled-deletions/process
# ----------------------------------------------------------------------
class TestScheduledDeletionsEndpoint:

    def test_404_when_cron_secret_not_configured(self, client):
        """If CRON_SECRET is not set, endpoint must look like it doesn't exist."""
        original = os.environ.pop("CRON_SECRET", None)
        try:
            resp = client.post(
                "/privacy/scheduled-deletions/process",
                headers={"X-Cron-Secret": "anything"},
            )
            assert resp.status_code == 404
        finally:
            if original is not None:
                os.environ["CRON_SECRET"] = original

    def test_401_when_secret_wrong(self, client):
        os.environ["CRON_SECRET"] = "the-right-secret"
        try:
            resp = client.post(
                "/privacy/scheduled-deletions/process",
                headers={"X-Cron-Secret": "the-wrong-secret"},
            )
            assert resp.status_code == 401
        finally:
            os.environ.pop("CRON_SECRET", None)

    @patch("app.api.routers.privacy.account_deletion.process_scheduled_deletions")
    def test_processes_when_secret_matches(self, mock_process, client):
        os.environ["CRON_SECRET"] = "the-right-secret"
        try:
            mock_process.return_value = {"processed": 3, "scanned": 3, "errors": []}
            resp = client.post(
                "/privacy/scheduled-deletions/process",
                headers={"X-Cron-Secret": "the-right-secret"},
            )
            assert resp.status_code == 200
            assert resp.json()["processed"] == 3
            mock_process.assert_called_once()
        finally:
            os.environ.pop("CRON_SECRET", None)


# ----------------------------------------------------------------------
# process_scheduled_deletions
# ----------------------------------------------------------------------
class TestProcessScheduledDeletions:

    @patch("app.services.account_deletion.delete_user_and_owned_data")
    @patch("app.services.account_deletion.get_supabase_client")
    def test_processes_only_overdue_pending_requests(self, mock_supa, mock_delete):
        supa = MagicMock()
        mock_supa.return_value = supa

        u1, u2 = str(uuid4()), str(uuid4())
        supa.table.return_value.select.return_value.in_.return_value.lte.return_value.execute.return_value = MagicMock(
            data=[
                {"id": "r1", "user_id": u1, "scheduled_deletion_at": "2020-01-01T00:00:00Z"},
                {"id": "r2", "user_id": u2, "scheduled_deletion_at": "2020-01-02T00:00:00Z"},
            ],
        )
        mock_delete.return_value = {"auth_user_deleted": True}

        result = account_deletion.process_scheduled_deletions()
        assert result["processed"] == 2
        assert result["scanned"] == 2
        called_user_ids = [c.args[0] for c in mock_delete.call_args_list]
        assert sorted(called_user_ids) == sorted([u1, u2])
