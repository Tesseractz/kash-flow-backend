"""
Tests for Supabase client module (supabase_client.py).
Covers client initialization and singleton pattern.
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestGetSupabaseClient:
    """Tests for get_supabase_client function."""
    
    def test_returns_client(self):
        """Test that a Supabase client is returned."""
        import app.supabase_client as sb_module
        
        # Reset singleton
        sb_module._client = None
        
        original_url = os.environ.get("SUPABASE_URL")
        original_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        
        os.environ["SUPABASE_URL"] = "https://test.supabase.co"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "test-service-key"
        
        with patch('app.supabase_client.create_client') as mock_create:
            mock_client = MagicMock()
            mock_create.return_value = mock_client
            
            try:
                result = sb_module.get_supabase_client()
                
                assert result == mock_client
                mock_create.assert_called_once_with("https://test.supabase.co", "test-service-key")
            finally:
                if original_url:
                    os.environ["SUPABASE_URL"] = original_url
                else:
                    os.environ.pop("SUPABASE_URL", None)
                if original_key:
                    os.environ["SUPABASE_SERVICE_ROLE_KEY"] = original_key
                else:
                    os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
                
                sb_module._client = None
    
    def test_returns_singleton(self):
        """Test that the same client instance is returned on subsequent calls."""
        import app.supabase_client as sb_module
        
        mock_client = MagicMock()
        sb_module._client = mock_client
        
        result1 = sb_module.get_supabase_client()
        result2 = sb_module.get_supabase_client()
        
        assert result1 is result2
        assert result1 is mock_client
        
        # Reset singleton
        sb_module._client = None
    
    def test_raises_on_missing_url(self):
        """Test that an error is raised when SUPABASE_URL is missing."""
        import app.supabase_client as sb_module
        
        original_url = os.environ.get("SUPABASE_URL")
        original_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        
        os.environ.pop("SUPABASE_URL", None)
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "test-key"
        
        try:
            # Mock both create_client and load_dotenv to prevent .env from overriding our test env
            with patch('app.supabase_client.create_client') as mock_create, \
                 patch('app.supabase_client.load_dotenv'):
                # Reset singleton inside the patch context
                sb_module._client = None
                
                # The error should be raised BEFORE create_client is called
                with pytest.raises(RuntimeError) as exc_info:
                    sb_module.get_supabase_client()
                
                # Verify error message
                assert "SUPABASE_URL" in str(exc_info.value) or "Missing" in str(exc_info.value)
                
                # create_client should NOT have been called
                mock_create.assert_not_called()
        finally:
            if original_url:
                os.environ["SUPABASE_URL"] = original_url
            if original_key:
                os.environ["SUPABASE_SERVICE_ROLE_KEY"] = original_key
            else:
                os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
            sb_module._client = None
    
    def test_raises_on_missing_key(self):
        """Test that an error is raised when SUPABASE_SERVICE_ROLE_KEY is missing."""
        import app.supabase_client as sb_module
        
        original_url = os.environ.get("SUPABASE_URL")
        original_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        
        os.environ["SUPABASE_URL"] = "https://test.supabase.co"
        os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
        
        try:
            # Mock both create_client and load_dotenv to prevent .env from overriding our test env
            with patch('app.supabase_client.create_client') as mock_create, \
                 patch('app.supabase_client.load_dotenv'):
                # Reset singleton inside the patch context
                sb_module._client = None
                
                # The error should be raised BEFORE create_client is called
                with pytest.raises(RuntimeError) as exc_info:
                    sb_module.get_supabase_client()
                
                # Verify error message
                assert "SUPABASE_SERVICE_ROLE_KEY" in str(exc_info.value) or "Missing" in str(exc_info.value)
                
                # create_client should NOT have been called
                mock_create.assert_not_called()
        finally:
            if original_url:
                os.environ["SUPABASE_URL"] = original_url
            else:
                os.environ.pop("SUPABASE_URL", None)
            if original_key:
                os.environ["SUPABASE_SERVICE_ROLE_KEY"] = original_key
            sb_module._client = None
