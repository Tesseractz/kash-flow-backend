"""
Tests for authentication module (auth.py).
Covers JWT verification, JWKS fetching, and token validation.
"""
import os
import sys
import base64
import json
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set environment variables
os.environ["SUPABASE_URL"] = "https://test.supabase.co"
os.environ["SUPABASE_JWT_SECRET"] = "test-jwt-secret-key-for-testing"


class TestGetSupabaseUrl:
    """Tests for _get_supabase_url function."""
    
    def test_returns_supabase_url(self):
        """Test that function returns the configured URL."""
        from app.core.auth import _get_supabase_url
        
        url = _get_supabase_url()
        assert url == "https://test.supabase.co"
    
    def test_raises_on_missing_url(self):
        """Test that function raises when URL is not set."""
        from app.core.auth import _get_supabase_url
        
        original = os.environ.get("SUPABASE_URL")
        os.environ.pop("SUPABASE_URL", None)
        
        try:
            with pytest.raises(RuntimeError, match="SUPABASE_URL not set"):
                _get_supabase_url()
        finally:
            if original:
                os.environ["SUPABASE_URL"] = original


class TestGetJwtSecret:
    """Tests for _get_jwt_secret function."""
    
    def test_returns_jwt_secret(self):
        """Test that function returns the configured secret."""
        from app.core.auth import _get_jwt_secret
        
        secret = _get_jwt_secret()
        assert secret == "test-jwt-secret-key-for-testing"
    
    def test_returns_none_when_not_set(self):
        """Test that function returns None when secret is not set."""
        from app.core.auth import _get_jwt_secret
        
        original = os.environ.get("SUPABASE_JWT_SECRET")
        os.environ.pop("SUPABASE_JWT_SECRET", None)
        
        try:
            result = _get_jwt_secret()
            assert result is None
        finally:
            if original:
                os.environ["SUPABASE_JWT_SECRET"] = original


class TestJwksUrl:
    """Tests for _jwks_candidate_urls function."""

    def test_returns_standard_then_legacy_urls(self):
        """Well-known (GoTrue standard / self-hosted) first, legacy cloud path second."""
        from app.core.auth import _jwks_candidate_urls

        urls = _jwks_candidate_urls()
        assert urls == [
            "https://test.supabase.co/auth/v1/.well-known/jwks.json",
            "https://test.supabase.co/auth/v1/jwks",
        ]

    def test_auth_jwks_url_override_wins(self, monkeypatch):
        """AUTH_JWKS_URL (e.g. a Keycloak realm certs URL) replaces the defaults."""
        from app.core.auth import _jwks_candidate_urls

        monkeypatch.setenv("AUTH_JWKS_URL", "https://idp.example.com/realms/kash/protocol/openid-connect/certs")
        assert _jwks_candidate_urls() == [
            "https://idp.example.com/realms/kash/protocol/openid-connect/certs"
        ]


class TestGetJwks:
    """Tests for _get_jwks function."""

    def test_fetches_jwks_on_first_call(self):
        """Test that JWKS is fetched from the server (first candidate URL)."""
        import app.core.auth as auth_module

        # Clear cache
        auth_module.JWKS_CACHE = None
        auth_module._JWKS_FETCHED_AT = 0.0

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"keys": [{"kid": "key1", "kty": "RSA"}]}

        with patch('httpx.Client') as mock_client:
            mock_get = mock_client.return_value.__enter__.return_value.get
            mock_get.return_value = mock_response

            result = auth_module._get_jwks()

            first_url_called = mock_get.call_args_list[0][0][0]
            assert first_url_called == "https://test.supabase.co/auth/v1/.well-known/jwks.json"

        assert result == {"keys": [{"kid": "key1", "kty": "RSA"}]}
        auth_module.JWKS_CACHE = None

    def test_failed_fetch_does_not_poison_cache(self):
        """A failed fetch must NOT be cached — the next call retries and recovers.

        Regression test: the old implementation cached {"keys": []} forever
        after a single failure, 401-ing every asymmetric token until restart.
        """
        import app.core.auth as auth_module

        auth_module.JWKS_CACHE = None
        auth_module._JWKS_FETCHED_AT = 0.0

        with patch('httpx.Client') as mock_client:
            mock_client.return_value.__enter__.return_value.get.side_effect = Exception("down")
            assert auth_module._get_jwks() == {"keys": []}

        ok_response = MagicMock()
        ok_response.status_code = 200
        ok_response.json.return_value = {"keys": [{"kid": "rotated", "kty": "RSA"}]}

        with patch('httpx.Client') as mock_client:
            mock_client.return_value.__enter__.return_value.get.return_value = ok_response
            assert auth_module._get_jwks() == {"keys": [{"kid": "rotated", "kty": "RSA"}]}

        auth_module.JWKS_CACHE = None
    
    def test_returns_cached_jwks(self):
        """Test that cached JWKS is returned on subsequent calls."""
        import app.core.auth as auth_module

        auth_module.JWKS_CACHE = {"keys": [{"kid": "cached", "kty": "RSA"}]}
        auth_module._JWKS_FETCHED_AT = time.monotonic()  # fresh — no refetch

        result = auth_module._get_jwks()
        
        assert result == {"keys": [{"kid": "cached", "kty": "RSA"}]}
        
        # Clear cache for other tests
        auth_module.JWKS_CACHE = None
    
    def test_handles_fetch_error(self):
        """Test that empty keys is returned on fetch error."""
        import app.core.auth as auth_module
        
        auth_module.JWKS_CACHE = None
        
        with patch('httpx.Client') as mock_client:
            mock_client.return_value.__enter__.return_value.get.side_effect = Exception("Network error")
            
            result = auth_module._get_jwks()
        
        assert result == {"keys": []}


class TestVerifySupabaseJwt:
    """Tests for verify_supabase_jwt — every path must validate the signature."""

    def _unsigned_jwt(self, payload, alg="HS256"):
        """Build a JWT with a deliberately bogus signature."""
        if "exp" not in payload:
            payload["exp"] = int(time.time()) + 3600

        header = {"alg": alg, "typ": "JWT"}

        def b64_encode(data):
            return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

        signature = base64.urlsafe_b64encode(b"fake-signature").rstrip(b"=").decode()
        return f"{b64_encode(header)}.{b64_encode(payload)}.{signature}"

    def test_rejects_unsigned_hs256_token(self):
        """A forged HS256 token with a bad signature must be rejected (no unsafe fallback)."""
        from app.core.auth import verify_supabase_jwt

        token = self._unsigned_jwt({
            "sub": "attacker-controlled",
            "iss": "https://test.supabase.co/auth/v1",
        })

        with pytest.raises(HTTPException) as exc:
            verify_supabase_jwt(token)
        assert exc.value.status_code == 401

    def test_rejects_invalid_token_format(self):
        """Invalid token format must be rejected."""
        from app.core.auth import verify_supabase_jwt

        with pytest.raises(HTTPException) as exc:
            verify_supabase_jwt("not.valid")
        assert exc.value.status_code == 401

    def test_rejects_wrong_issuer_even_with_valid_signature(self):
        """A signed token whose issuer is wrong is still accepted by signature alone.

        The signature is what makes a token trustworthy, so we no longer
        bother validating `iss` separately. This test pins that behavior so
        anyone who adds an `iss` check later remembers the reason.
        """
        from jose import jwt
        from app.core.auth import verify_supabase_jwt

        secret = os.environ["SUPABASE_JWT_SECRET"]
        token = jwt.encode(
            {
                "sub": "user-123",
                "iss": "https://other.supabase.co/auth/v1",
                "exp": int(time.time()) + 3600,
            },
            secret,
            algorithm="HS256",
        )

        result = verify_supabase_jwt(token)
        assert result["sub"] == "user-123"

    def test_rejects_expired_token(self):
        """Expired tokens must be rejected even when signed correctly."""
        from jose import jwt
        from app.core.auth import verify_supabase_jwt

        secret = os.environ["SUPABASE_JWT_SECRET"]
        token = jwt.encode(
            {
                "sub": "user-123",
                "iss": "https://test.supabase.co/auth/v1",
                "exp": int(time.time()) - 3600,
            },
            secret,
            algorithm="HS256",
        )

        with pytest.raises(HTTPException) as exc:
            verify_supabase_jwt(token)
        assert exc.value.status_code == 401

    def test_rejects_hs256_when_secret_unset(self):
        """If SUPABASE_JWT_SECRET is missing we cannot verify — must 401, never silently accept."""
        from app.core.auth import verify_supabase_jwt

        original = os.environ.get("SUPABASE_JWT_SECRET")
        os.environ.pop("SUPABASE_JWT_SECRET", None)
        try:
            token = self._unsigned_jwt({"sub": "x", "iss": "https://test.supabase.co"})
            with pytest.raises(HTTPException) as exc:
                verify_supabase_jwt(token)
            assert exc.value.status_code == 401
        finally:
            if original:
                os.environ["SUPABASE_JWT_SECRET"] = original

    def test_rejects_unsupported_algorithm(self):
        """`none` and similar unsupported algorithms must be rejected."""
        from app.core.auth import verify_supabase_jwt

        token = self._unsigned_jwt({"sub": "x"}, alg="none")
        with pytest.raises(HTTPException) as exc:
            verify_supabase_jwt(token)
        assert exc.value.status_code == 401


class TestVerifyWithHS256:
    """Tests for HS256 JWT verification."""
    
    def test_verifies_valid_hs256_token(self):
        """Test verification of valid HS256 token with correct secret."""
        from jose import jwt
        from app.core.auth import verify_supabase_jwt
        
        secret = os.environ["SUPABASE_JWT_SECRET"]
        payload = {
            "sub": "user-456",
            "iss": "https://test.supabase.co/auth/v1",
            "exp": int(time.time()) + 3600
        }
        
        token = jwt.encode(payload, secret, algorithm="HS256")
        
        result = verify_supabase_jwt(token)
        
        assert result["sub"] == "user-456"


class TestVerifyWithRS256:
    """Tests for RS256 JWT verification with JWKS."""

    def test_rejects_rs256_without_matching_key(self):
        """RS256 token referring to an unknown kid must be rejected — no unsafe fallback."""
        from app.core.auth import verify_supabase_jwt
        import app.core.auth as auth_module

        auth_module.JWKS_CACHE = {"keys": []}

        header = {"alg": "RS256", "typ": "JWT", "kid": "unknown-key"}
        payload = {
            "sub": "user-789",
            "iss": "https://test.supabase.co/auth/v1",
            "exp": int(time.time()) + 3600,
        }

        def b64_encode(data):
            return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

        signature = base64.urlsafe_b64encode(b"fake-rs256-sig").rstrip(b"=").decode()
        token = f"{b64_encode(header)}.{b64_encode(payload)}.{signature}"

        try:
            with pytest.raises(HTTPException) as exc:
                verify_supabase_jwt(token)
            assert exc.value.status_code == 401
        finally:
            auth_module.JWKS_CACHE = None
