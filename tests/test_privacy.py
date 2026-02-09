"""
Tests for Privacy & Compliance API endpoints.
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from uuid import uuid4

from app.main import app
from app.deps import get_current_context, RequestContext


@pytest.fixture
def user_context():
    """Returns a mock user context."""
    return RequestContext(
        user_id=str(uuid4()),
        store_id=str(uuid4()),
        role="cashier"
    )


@pytest.fixture
def admin_context():
    """Returns a mock admin context."""
    return RequestContext(
        user_id=str(uuid4()),
        store_id=str(uuid4()),
        role="admin"
    )


@pytest.fixture
def client():
    """Returns a test client."""
    return TestClient(app)


# ============================================
# CONSENT TESTS
# ============================================
class TestConsentsAPI:
    
    @patch("app.main.get_supabase_client")
    def test_get_user_consents(self, mock_supabase, client, user_context):
        """User can get their consent records."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        
        consents_data = [
            {"id": str(uuid4()), "user_id": user_context.user_id, "consent_type": "terms", "consented": True},
            {"id": str(uuid4()), "user_id": user_context.user_id, "consent_type": "privacy", "consented": True},
        ]
        mock_client.table().select().eq().execute.return_value = MagicMock(data=consents_data)
        
        app.dependency_overrides[get_current_context] = lambda: user_context
        try:
            response = client.get("/privacy/consents")
            assert response.status_code == 200
            assert len(response.json()) == 2
        finally:
            app.dependency_overrides.clear()
    
    @patch("app.main.get_supabase_client")
    @patch("app.main.log_audit_event")
    def test_update_consent(self, mock_audit, mock_supabase, client, user_context):
        """User can update their consent."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        
        consent_id = str(uuid4())
        mock_client.table().upsert().execute.return_value = MagicMock(data=[{
            "id": consent_id,
            "user_id": user_context.user_id,
            "consent_type": "marketing",
            "consented": True,
            "consent_version": "1.0"
        }])
        
        app.dependency_overrides[get_current_context] = lambda: user_context
        try:
            response = client.post("/privacy/consents", json={
                "consent_type": "marketing",
                "consented": True,
                "consent_version": "1.0"
            })
            assert response.status_code == 200
            assert response.json()["consent_type"] == "marketing"
            assert response.json()["consented"] is True
        finally:
            app.dependency_overrides.clear()


# ============================================
# PRIVACY SETTINGS TESTS
# ============================================
class TestPrivacySettingsAPI:
    
    @patch("app.main.get_supabase_client")
    def test_get_privacy_settings(self, mock_supabase, client, user_context):
        """User can get their privacy settings."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        
        mock_client.table().select().eq().single().execute.return_value = MagicMock(data={
            "marketing_emails_enabled": False,
            "push_notifications_enabled": True,
            "data_analytics_enabled": True,
            "two_factor_enabled": False
        })
        
        app.dependency_overrides[get_current_context] = lambda: user_context
        try:
            response = client.get("/privacy/settings")
            assert response.status_code == 200
            assert response.json()["push_notifications_enabled"] is True
            assert response.json()["marketing_emails_enabled"] is False
        finally:
            app.dependency_overrides.clear()
    
    @patch("app.main.get_supabase_client")
    @patch("app.main.log_audit_event")
    def test_update_privacy_settings(self, mock_audit, mock_supabase, client, user_context):
        """User can update privacy settings."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        
        mock_client.table().update().eq().execute.return_value = MagicMock(data=[{}])
        mock_client.table().select().eq().single().execute.return_value = MagicMock(data={
            "marketing_emails_enabled": True,
            "push_notifications_enabled": True,
            "data_analytics_enabled": True,
            "two_factor_enabled": False
        })
        
        app.dependency_overrides[get_current_context] = lambda: user_context
        try:
            response = client.put("/privacy/settings", json={
                "marketing_emails_enabled": True
            })
            assert response.status_code == 200
        finally:
            app.dependency_overrides.clear()


# ============================================
# SESSION TESTS
# ============================================
class TestSessionsAPI:
    
    @patch("app.main.get_supabase_client")
    def test_get_sessions(self, mock_supabase, client, user_context):
        """User can get their active sessions."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        
        sessions_data = [
            {
                "id": str(uuid4()),
                "user_id": user_context.user_id,
                "device_info": {"browser": "Chrome", "os": "Windows"},
                "last_active_at": datetime.now(timezone.utc).isoformat(),
                "is_current": True
            }
        ]
        mock_client.table().select().eq().order().execute.return_value = MagicMock(data=sessions_data)
        
        app.dependency_overrides[get_current_context] = lambda: user_context
        try:
            response = client.get("/privacy/sessions")
            assert response.status_code == 200
            assert len(response.json()) == 1
            assert response.json()[0]["is_current"] is True
        finally:
            app.dependency_overrides.clear()
    
    @patch("app.main.get_supabase_client")
    @patch("app.main.log_audit_event")
    def test_revoke_session(self, mock_audit, mock_supabase, client, user_context):
        """User can revoke a specific session."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        mock_client.table().delete().eq().eq().execute.return_value = MagicMock()
        
        session_id = str(uuid4())
        
        app.dependency_overrides[get_current_context] = lambda: user_context
        try:
            response = client.delete(f"/privacy/sessions/{session_id}")
            assert response.status_code == 204
        finally:
            app.dependency_overrides.clear()
    
    @patch("app.main.get_supabase_client")
    @patch("app.main.log_audit_event")
    def test_revoke_all_sessions(self, mock_audit, mock_supabase, client, user_context):
        """User can revoke all other sessions."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        mock_client.table().delete().eq().eq().execute.return_value = MagicMock()
        
        app.dependency_overrides[get_current_context] = lambda: user_context
        try:
            response = client.delete("/privacy/sessions")
            assert response.status_code == 204
        finally:
            app.dependency_overrides.clear()


# ============================================
# DATA EXPORT TESTS
# ============================================
class TestDataExportAPI:
    
    @patch("app.main.get_supabase_client")
    @patch("app.main.log_audit_event")
    def test_request_data_export(self, mock_audit, mock_supabase, client, user_context):
        """User can request data export."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        
        # No pending requests
        mock_client.table().select().eq().eq().execute.return_value = MagicMock(data=[])
        
        request_id = str(uuid4())
        mock_client.table().insert().execute.return_value = MagicMock(data=[{
            "id": request_id,
            "user_id": user_context.user_id,
            "status": "pending",
            "requested_at": datetime.now(timezone.utc).isoformat()
        }])
        
        app.dependency_overrides[get_current_context] = lambda: user_context
        try:
            response = client.post("/privacy/data-export")
            assert response.status_code == 201
            assert response.json()["status"] == "pending"
        finally:
            app.dependency_overrides.clear()
    
    @patch("app.main.get_supabase_client")
    def test_request_data_export_already_pending(self, mock_supabase, client, user_context):
        """Cannot request export if one is pending."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        
        # Has pending request
        mock_client.table().select().eq().eq().execute.return_value = MagicMock(data=[{"id": str(uuid4())}])
        
        app.dependency_overrides[get_current_context] = lambda: user_context
        try:
            response = client.post("/privacy/data-export")
            assert response.status_code == 400
            assert "already have a pending" in response.json()["detail"]
        finally:
            app.dependency_overrides.clear()
    
    @patch("app.main.get_supabase_client")
    def test_get_data_export_requests(self, mock_supabase, client, user_context):
        """User can get their export requests."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        
        requests_data = [
            {"id": str(uuid4()), "status": "completed", "download_url": "https://..."},
        ]
        mock_client.table().select().eq().order().execute.return_value = MagicMock(data=requests_data)
        
        app.dependency_overrides[get_current_context] = lambda: user_context
        try:
            response = client.get("/privacy/data-export")
            assert response.status_code == 200
            assert len(response.json()) == 1
        finally:
            app.dependency_overrides.clear()


# ============================================
# ACCOUNT DELETION TESTS
# ============================================
class TestAccountDeletionAPI:
    
    @patch("app.main.get_supabase_client")
    @patch("app.main.log_audit_event")
    def test_request_account_deletion(self, mock_audit, mock_supabase, client, user_context):
        """User can request account deletion."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        
        # No pending requests
        mock_client.table().select().eq().in_().execute.return_value = MagicMock(data=[])
        
        request_id = str(uuid4())
        mock_client.table().insert().execute.return_value = MagicMock(data=[{
            "id": request_id,
            "user_id": user_context.user_id,
            "reason": "Moving to another platform",
            "status": "pending",
            "requested_at": datetime.now(timezone.utc).isoformat(),
            "scheduled_deletion_at": "2026-03-11T00:00:00Z"
        }])
        
        app.dependency_overrides[get_current_context] = lambda: user_context
        try:
            response = client.post("/privacy/delete-account", json={
                "reason": "Moving to another platform",
                "confirm_password": "mypassword123"
            })
            assert response.status_code == 201
            assert response.json()["status"] == "pending"
        finally:
            app.dependency_overrides.clear()
    
    @patch("app.main.get_supabase_client")
    def test_request_deletion_already_pending(self, mock_supabase, client, user_context):
        """Cannot request deletion if one is pending."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        
        mock_client.table().select().eq().in_().execute.return_value = MagicMock(data=[{"id": str(uuid4())}])
        
        app.dependency_overrides[get_current_context] = lambda: user_context
        try:
            response = client.post("/privacy/delete-account", json={
                "reason": "Test",
                "confirm_password": "password"
            })
            assert response.status_code == 400
            assert "already have a pending" in response.json()["detail"]
        finally:
            app.dependency_overrides.clear()
    
    @patch("app.main.get_supabase_client")
    @patch("app.main.log_audit_event")
    def test_cancel_account_deletion(self, mock_audit, mock_supabase, client, user_context):
        """User can cancel a pending deletion request."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        
        request_id = str(uuid4())
        mock_client.table().update().eq().eq().in_().execute.return_value = MagicMock(data=[{
            "id": request_id,
            "status": "cancelled"
        }])
        
        app.dependency_overrides[get_current_context] = lambda: user_context
        try:
            response = client.delete(f"/privacy/delete-account/{request_id}")
            assert response.status_code == 204
        finally:
            app.dependency_overrides.clear()


# ============================================
# COOKIE PREFERENCES TESTS
# ============================================
class TestCookiePreferencesAPI:
    
    @patch("app.main.get_supabase_client")
    def test_save_cookie_preferences(self, mock_supabase, client, user_context):
        """User can save cookie preferences."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        
        mock_client.table().upsert().execute.return_value = MagicMock(data=[{
            "user_id": user_context.user_id,
            "essential": True,
            "analytics": True,
            "marketing": False,
            "functional": True
        }])
        
        app.dependency_overrides[get_current_context] = lambda: user_context
        try:
            response = client.post("/privacy/cookies", json={
                "essential": True,
                "analytics": True,
                "marketing": False,
                "functional": True
            })
            assert response.status_code == 200
            assert response.json()["essential"] is True
            assert response.json()["analytics"] is True
            assert response.json()["marketing"] is False
        finally:
            app.dependency_overrides.clear()
    
    @patch("app.main.get_supabase_client")
    def test_get_cookie_preferences(self, mock_supabase, client, user_context):
        """User can get cookie preferences."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        
        mock_client.table().select().eq().single().execute.return_value = MagicMock(data={
            "analytics": True,
            "marketing": False,
            "functional": True
        })
        
        app.dependency_overrides[get_current_context] = lambda: user_context
        try:
            response = client.get("/privacy/cookies")
            assert response.status_code == 200
            assert response.json()["essential"] is True  # Always true
            assert response.json()["analytics"] is True
        finally:
            app.dependency_overrides.clear()
    
    @patch("app.main.get_supabase_client")
    def test_get_cookie_preferences_default(self, mock_supabase, client, user_context):
        """Returns default preferences if none set."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        
        mock_client.table().select().eq().single().execute.return_value = MagicMock(data=None)
        
        app.dependency_overrides[get_current_context] = lambda: user_context
        try:
            response = client.get("/privacy/cookies")
            assert response.status_code == 200
            assert response.json()["essential"] is True
            assert response.json()["analytics"] is False
            assert response.json()["marketing"] is False
            assert response.json()["functional"] is True
        finally:
            app.dependency_overrides.clear()


# ============================================
# LEGAL DOCUMENTS TESTS
# ============================================
class TestLegalAPI:
    
    def test_get_terms_of_service(self, client, user_context):
        """Anyone can get terms of service info."""
        app.dependency_overrides[get_current_context] = lambda: user_context
        try:
            response = client.get("/legal/terms")
            assert response.status_code == 200
            assert "version" in response.json()
            assert "effective_date" in response.json()
        finally:
            app.dependency_overrides.clear()
    
    def test_get_privacy_policy(self, client, user_context):
        """Anyone can get privacy policy info."""
        app.dependency_overrides[get_current_context] = lambda: user_context
        try:
            response = client.get("/legal/privacy")
            assert response.status_code == 200
            assert "version" in response.json()
            assert "effective_date" in response.json()
        finally:
            app.dependency_overrides.clear()
