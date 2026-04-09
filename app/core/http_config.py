import os
from urllib.parse import urlparse

from fastapi import Request

cors_origins_env = os.getenv("BACKEND_CORS_ORIGINS", "")
allowed_origins = (
    [o.strip() for o in cors_origins_env.split(",") if o.strip()]
    if cors_origins_env
    else ["*"]
)


def normalize_frontend_base(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    if not u:
        return ""
    p = urlparse(u)
    if p.scheme and p.netloc:
        return f"{p.scheme}://{p.netloc}".rstrip("/")
    return u


def is_loopback_http_origin(url: str) -> bool:
    """True for http(s)://localhost, 127.0.0.1, or ::1 (typical local Vite/SPA dev)."""
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return False
    h = (p.hostname or "").lower()
    return h in ("localhost", "127.0.0.1", "::1")


def resolve_frontend_base_url(request: Request) -> str:
    """
    Paystack redirects back to the SPA. Prefer the browser Origin (or X-App-Origin)
    when it is trusted, so local dev works even if FRONTEND_URL in .env is wrong (e.g. :5001 vs :5000).
    """
    env_base = normalize_frontend_base(os.getenv("FRONTEND_URL", "http://localhost:5000"))
    if not env_base:
        env_base = "http://localhost:5000"

    raw = (request.headers.get("x-app-origin") or request.headers.get("origin") or "").strip()
    candidate = normalize_frontend_base(raw)
    if not candidate:
        return env_base

    host = (urlparse(candidate).hostname or "").lower()
    if allowed_origins == ["*"]:
        if host in ("localhost", "127.0.0.1", "::1") and urlparse(candidate).scheme in ("http", "https"):
            return candidate
        return env_base

    allowed_norm = {normalize_frontend_base(o) for o in allowed_origins}
    if candidate in allowed_norm:
        return candidate
    if is_loopback_http_origin(candidate) and is_loopback_http_origin(env_base):
        return candidate
    return env_base
