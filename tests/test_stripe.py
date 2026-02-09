"""
Tests for Stripe client module (stripe_client.py).
Covers credential fetching and Stripe initialization.
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestGetStripeCredentialsFromEnv:
    """Tests for get_stripe_credentials_from_env function."""
    
    def test_returns_credentials_when_set(self):
        """Test that credentials are returned when environment variables are set."""
        from app.stripe_client import get_stripe_credentials_from_env
        
        original_secret = os.environ.get("STRIPE_SECRET_KEY")
        original_pub = os.environ.get("STRIPE_PUBLISHABLE_KEY")
        
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_123"
        os.environ["STRIPE_PUBLISHABLE_KEY"] = "pk_test_456"
        
        try:
            result = get_stripe_credentials_from_env()
            
            assert result is not None
            assert result["secret_key"] == "sk_test_123"
            assert result["publishable_key"] == "pk_test_456"
        finally:
            if original_secret:
                os.environ["STRIPE_SECRET_KEY"] = original_secret
            else:
                os.environ.pop("STRIPE_SECRET_KEY", None)
            if original_pub:
                os.environ["STRIPE_PUBLISHABLE_KEY"] = original_pub
            else:
                os.environ.pop("STRIPE_PUBLISHABLE_KEY", None)
    
    def test_returns_none_when_not_set(self):
        """Test that None is returned when STRIPE_SECRET_KEY is not set."""
        from app.stripe_client import get_stripe_credentials_from_env
        
        original = os.environ.get("STRIPE_SECRET_KEY")
        os.environ.pop("STRIPE_SECRET_KEY", None)
        
        try:
            result = get_stripe_credentials_from_env()
            assert result is None
        finally:
            if original:
                os.environ["STRIPE_SECRET_KEY"] = original
    
    def test_handles_empty_publishable_key(self):
        """Test that empty publishable key is handled."""
        from app.stripe_client import get_stripe_credentials_from_env
        
        original_secret = os.environ.get("STRIPE_SECRET_KEY")
        original_pub = os.environ.get("STRIPE_PUBLISHABLE_KEY")
        
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_789"
        os.environ.pop("STRIPE_PUBLISHABLE_KEY", None)
        
        try:
            result = get_stripe_credentials_from_env()
            
            assert result is not None
            assert result["secret_key"] == "sk_test_789"
            assert result["publishable_key"] == ""
        finally:
            if original_secret:
                os.environ["STRIPE_SECRET_KEY"] = original_secret
            else:
                os.environ.pop("STRIPE_SECRET_KEY", None)
            if original_pub:
                os.environ["STRIPE_PUBLISHABLE_KEY"] = original_pub


class TestGetStripeCredentialsFromReplit:
    """Tests for get_stripe_credentials_from_replit function."""
    
    def test_returns_none_when_not_in_replit(self):
        """Test that None is returned when not in Replit environment."""
        from app.stripe_client import get_stripe_credentials_from_replit
        
        # Clear Replit-specific environment variables
        originals = {}
        for key in ["REPLIT_CONNECTORS_HOSTNAME", "REPL_IDENTITY", "WEB_REPL_RENEWAL"]:
            originals[key] = os.environ.get(key)
            os.environ.pop(key, None)
        
        try:
            result = get_stripe_credentials_from_replit()
            assert result is None
        finally:
            for key, value in originals.items():
                if value:
                    os.environ[key] = value
    
    @patch('httpx.get')
    def test_fetches_from_replit_api(self, mock_get):
        """Test that credentials are fetched from Replit connector API."""
        from app.stripe_client import get_stripe_credentials_from_replit
        
        originals = {
            "REPLIT_CONNECTORS_HOSTNAME": os.environ.get("REPLIT_CONNECTORS_HOSTNAME"),
            "REPL_IDENTITY": os.environ.get("REPL_IDENTITY"),
        }
        
        os.environ["REPLIT_CONNECTORS_HOSTNAME"] = "connectors.replit.com"
        os.environ["REPL_IDENTITY"] = "test-identity"
        
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "items": [{
                "settings": {
                    "secret": "sk_test_replit",
                    "publishable": "pk_test_replit"
                }
            }]
        }
        mock_get.return_value = mock_response
        
        try:
            result = get_stripe_credentials_from_replit()
            
            assert result is not None
            assert result["secret_key"] == "sk_test_replit"
            assert result["publishable_key"] == "pk_test_replit"
        finally:
            for key, value in originals.items():
                if value:
                    os.environ[key] = value
                else:
                    os.environ.pop(key, None)


class TestGetStripeCredentials:
    """Tests for get_stripe_credentials function."""
    
    def test_prefers_env_credentials(self):
        """Test that environment credentials are preferred."""
        from app.stripe_client import get_stripe_credentials
        
        original_secret = os.environ.get("STRIPE_SECRET_KEY")
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_env"
        
        try:
            result = get_stripe_credentials()
            
            assert result["secret_key"] == "sk_test_env"
        finally:
            if original_secret:
                os.environ["STRIPE_SECRET_KEY"] = original_secret
            else:
                os.environ.pop("STRIPE_SECRET_KEY", None)
    
    def test_raises_when_no_credentials(self):
        """Test that exception is raised when no credentials found."""
        from app.stripe_client import get_stripe_credentials
        
        original_secret = os.environ.get("STRIPE_SECRET_KEY")
        os.environ.pop("STRIPE_SECRET_KEY", None)
        
        # Clear Replit vars too
        replit_originals = {}
        for key in ["REPLIT_CONNECTORS_HOSTNAME", "REPL_IDENTITY", "WEB_REPL_RENEWAL"]:
            replit_originals[key] = os.environ.get(key)
            os.environ.pop(key, None)
        
        try:
            with pytest.raises(Exception, match="Stripe credentials not found"):
                get_stripe_credentials()
        finally:
            if original_secret:
                os.environ["STRIPE_SECRET_KEY"] = original_secret
            for key, value in replit_originals.items():
                if value:
                    os.environ[key] = value


class TestInitStripe:
    """Tests for init_stripe function."""
    
    def test_initializes_stripe(self):
        """Test that Stripe is initialized with credentials."""
        import app.stripe_client as stripe_module
        
        # Reset module state
        stripe_module._stripe_initialized = False
        stripe_module._publishable_key = None
        
        original_secret = os.environ.get("STRIPE_SECRET_KEY")
        original_pub = os.environ.get("STRIPE_PUBLISHABLE_KEY")
        
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_init"
        os.environ["STRIPE_PUBLISHABLE_KEY"] = "pk_test_init"
        
        try:
            stripe_module.init_stripe()
            
            assert stripe_module._stripe_initialized is True
            assert stripe_module._publishable_key == "pk_test_init"
        finally:
            if original_secret:
                os.environ["STRIPE_SECRET_KEY"] = original_secret
            else:
                os.environ.pop("STRIPE_SECRET_KEY", None)
            if original_pub:
                os.environ["STRIPE_PUBLISHABLE_KEY"] = original_pub
            else:
                os.environ.pop("STRIPE_PUBLISHABLE_KEY", None)
            
            # Reset for other tests
            stripe_module._stripe_initialized = False
    
    def test_skips_if_already_initialized(self):
        """Test that initialization is skipped if already done."""
        import app.stripe_client as stripe_module
        
        stripe_module._stripe_initialized = True
        stripe_module._publishable_key = "pk_existing"
        
        # This should not change the existing state
        stripe_module.init_stripe()
        
        assert stripe_module._publishable_key == "pk_existing"
        
        # Reset for other tests
        stripe_module._stripe_initialized = False


class TestGetStripeClient:
    """Tests for get_stripe_client function."""
    
    def test_returns_stripe_module(self):
        """Test that Stripe module is returned."""
        import app.stripe_client as stripe_module
        import stripe
        
        stripe_module._stripe_initialized = False
        
        original_secret = os.environ.get("STRIPE_SECRET_KEY")
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_client"
        
        try:
            result = stripe_module.get_stripe_client()
            
            assert result == stripe
        finally:
            if original_secret:
                os.environ["STRIPE_SECRET_KEY"] = original_secret
            else:
                os.environ.pop("STRIPE_SECRET_KEY", None)
            
            stripe_module._stripe_initialized = False


class TestGetPublishableKey:
    """Tests for get_publishable_key function."""
    
    def test_returns_publishable_key(self):
        """Test that publishable key is returned."""
        import app.stripe_client as stripe_module
        
        stripe_module._stripe_initialized = False
        
        original_secret = os.environ.get("STRIPE_SECRET_KEY")
        original_pub = os.environ.get("STRIPE_PUBLISHABLE_KEY")
        
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_pub"
        os.environ["STRIPE_PUBLISHABLE_KEY"] = "pk_test_pub"
        
        try:
            result = stripe_module.get_publishable_key()
            
            assert result == "pk_test_pub"
        finally:
            if original_secret:
                os.environ["STRIPE_SECRET_KEY"] = original_secret
            else:
                os.environ.pop("STRIPE_SECRET_KEY", None)
            if original_pub:
                os.environ["STRIPE_PUBLISHABLE_KEY"] = original_pub
            else:
                os.environ.pop("STRIPE_PUBLISHABLE_KEY", None)
            
            stripe_module._stripe_initialized = False


class TestGetWebhookSecret:
    """Tests for get_webhook_secret function."""
    
    def test_returns_webhook_secret(self):
        """Test that webhook secret is returned."""
        from app.stripe_client import get_webhook_secret
        
        original = os.environ.get("STRIPE_WEBHOOK_SECRET")
        os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_test123"
        
        try:
            result = get_webhook_secret()
            assert result == "whsec_test123"
        finally:
            if original:
                os.environ["STRIPE_WEBHOOK_SECRET"] = original
            else:
                os.environ.pop("STRIPE_WEBHOOK_SECRET", None)
    
    def test_returns_none_when_not_set(self):
        """Test that None is returned when not set."""
        from app.stripe_client import get_webhook_secret
        
        original = os.environ.get("STRIPE_WEBHOOK_SECRET")
        os.environ.pop("STRIPE_WEBHOOK_SECRET", None)
        
        try:
            result = get_webhook_secret()
            assert result is None
        finally:
            if original:
                os.environ["STRIPE_WEBHOOK_SECRET"] = original
