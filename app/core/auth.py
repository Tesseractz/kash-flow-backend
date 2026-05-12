import base64
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from dotenv import load_dotenv
from fastapi import HTTPException, status
from jose import JWTError, jwt

# Ensure environment variables from backend/.env are available at import time,
# so JWT verification has SUPABASE_URL and SUPABASE_JWT_SECRET regardless of CWD.
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
_env_loaded = load_dotenv(dotenv_path=_ENV_PATH, override=False)

if not os.getenv("SUPABASE_URL"):
    print("[Auth Init] WARNING: SUPABASE_URL not set")
if not os.getenv("SUPABASE_JWT_SECRET"):
    print("[Auth Init] WARNING: SUPABASE_JWT_SECRET not set")

JWKS_CACHE: Optional[Dict[str, Any]] = None


def _get_supabase_url() -> str:
    base = os.getenv("SUPABASE_URL")
    if not base:
        raise RuntimeError("SUPABASE_URL not set")
    return base.strip()


def _get_jwt_secret() -> Optional[str]:
    secret = os.getenv("SUPABASE_JWT_SECRET")
    if secret:
        return secret.strip()
    return None


def _jwks_url() -> str:
    return f"{_get_supabase_url()}/auth/v1/jwks"


def _get_jwks() -> Dict[str, Any]:
    global JWKS_CACHE
    if JWKS_CACHE is None:
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.get(_jwks_url())
                resp.raise_for_status()
                JWKS_CACHE = resp.json()
        except Exception:
            JWKS_CACHE = {"keys": []}
    return JWKS_CACHE


_ASYMMETRIC_ALGS = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512")
_AUTH_DEBUG = os.getenv("AUTH_DEBUG", "").strip() in ("1", "true", "yes", "on")


def _log(msg: str) -> None:
    if _AUTH_DEBUG:
        print(f"[Auth] {msg}")


def verify_supabase_jwt(token: str) -> Dict[str, Any]:
    """Verify a Supabase access token.

    Tries HS256 with `SUPABASE_JWT_SECRET` (raw and base64-decoded), then
    falls through to JWKS-based asymmetric verification (RS256/RS384/RS512,
    ES256/ES384/ES512 — Supabase rolled out ECC keys in late 2024).

    **Never** accepts an unverified token — a forged token would otherwise be
    granted full access to the authenticated user's store via
    `RequestContext.store_id`.

    Set AUTH_DEBUG=1 in the env to print the failure reason for each rejected
    token (useful when debugging "every endpoint returns 401" issues).
    """
    try:
        unverified = jwt.get_unverified_header(token)
        alg = unverified.get("alg", "HS256")
        kid = unverified.get("kid")
        _log(f"verifying token alg={alg} kid={kid}")

        jwt_secret = _get_jwt_secret()

        if alg == "HS256":
            if not jwt_secret:
                _log("HS256 token but SUPABASE_JWT_SECRET is not set")
                raise HTTPException(
                    status_code=401,
                    detail="Server missing SUPABASE_JWT_SECRET; cannot verify HS256 token.",
                )
            try:
                return jwt.decode(
                    token,
                    jwt_secret,
                    algorithms=["HS256"],
                    options={"verify_aud": False},
                )
            except JWTError as e:
                _log(f"HS256 raw-secret decode failed: {e}")

            try:
                decoded_secret = base64.b64decode(jwt_secret)
                return jwt.decode(
                    token,
                    decoded_secret,
                    algorithms=["HS256"],
                    options={"verify_aud": False},
                )
            except (JWTError, ValueError, TypeError) as e:
                _log(f"HS256 base64-secret decode failed: {e}")
                raise HTTPException(status_code=401, detail="Invalid or expired token")

        if alg in _ASYMMETRIC_ALGS:
            jwks = _get_jwks()
            key = None
            for jwk in jwks.get("keys", []):
                if jwk.get("kid") == kid:
                    key = jwk
                    break
            if not key:
                available = [k.get("kid") for k in jwks.get("keys", [])]
                _log(f"no JWKS key matched kid={kid!r}; jwks served {available}")
                raise HTTPException(status_code=401, detail="Unknown signing key")
            try:
                return jwt.decode(
                    token,
                    key,
                    algorithms=[alg],
                    options={"verify_aud": False},
                )
            except JWTError as e:
                _log(f"{alg} JWKS decode failed (kid={kid}): {e}")
                raise HTTPException(status_code=401, detail="Invalid or expired token")

        _log(f"unsupported algorithm: {alg}")
        raise HTTPException(status_code=401, detail=f"Unsupported token algorithm: {alg}")

    except HTTPException:
        raise
    except JWTError as e:
        _log(f"unexpected JWTError: {e}")
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    except Exception as e:
        _log(f"unexpected error: {type(e).__name__}: {e}")
        raise HTTPException(status_code=401, detail="Invalid or expired token")
