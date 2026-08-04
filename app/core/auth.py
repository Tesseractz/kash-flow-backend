import base64
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

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
_JWKS_FETCHED_AT: float = 0.0
JWKS_TTL_SECONDS: float = 600.0  # refresh at most every 10 min; also on unknown kid


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


def _jwks_candidate_urls() -> List[str]:
    """JWKS endpoints to try, most standard first.

    `AUTH_JWKS_URL` overrides everything (used when the IdP is not Supabase,
    e.g. Keycloak). Otherwise we try GoTrue's standard well-known path (what
    self-hosted stacks and the Supabase CLI expose) and fall back to the
    legacy cloud path.
    """
    override = os.getenv("AUTH_JWKS_URL", "").strip()
    if override:
        return [override]
    base = _get_supabase_url()
    return [
        f"{base}/auth/v1/.well-known/jwks.json",
        f"{base}/auth/v1/jwks",
    ]


def _jwks_request_headers() -> Dict[str, str]:
    # Kong in self-hosted/local stacks requires an apikey even for JWKS.
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if key:
        return {"apikey": key, "Authorization": f"Bearer {key}"}
    return {}


def _fetch_jwks() -> Optional[Dict[str, Any]]:
    for url in _jwks_candidate_urls():
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.get(url, headers=_jwks_request_headers())
                if resp.status_code != 200:
                    _log(f"JWKS fetch {url} -> HTTP {resp.status_code}")
                    continue
                data = resp.json()
                if data.get("keys"):
                    return data
                _log(f"JWKS fetch {url} -> empty key set")
        except Exception as e:
            _log(f"JWKS fetch {url} failed: {type(e).__name__}: {e}")
    return None


def _get_jwks(force: bool = False) -> Dict[str, Any]:
    """Return the JWKS, caching successes for JWKS_TTL_SECONDS.

    A failed fetch NEVER poisons the cache: if we have a previous good key
    set we keep serving it; if we have nothing we return empty for this
    request only and retry on the next one.
    """
    global JWKS_CACHE, _JWKS_FETCHED_AT
    now = time.monotonic()
    expired = (now - _JWKS_FETCHED_AT) > JWKS_TTL_SECONDS
    if JWKS_CACHE is None or force or expired:
        fresh = _fetch_jwks()
        if fresh is not None:
            JWKS_CACHE = fresh
            _JWKS_FETCHED_AT = now
        elif JWKS_CACHE is None:
            return {"keys": []}
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
            def _find_key(jwks: Dict[str, Any]) -> Optional[Dict[str, Any]]:
                for jwk in jwks.get("keys", []):
                    if jwk.get("kid") == kid:
                        return jwk
                return None

            jwks = _get_jwks()
            key = _find_key(jwks)
            if not key:
                # Unknown kid — the signing key may have rotated since our
                # cached fetch (or the cache is empty). Refresh once and retry.
                jwks = _get_jwks(force=True)
                key = _find_key(jwks)
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
