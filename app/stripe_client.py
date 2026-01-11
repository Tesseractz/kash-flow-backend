"""
Stripe client for POS backend.
Supports both local development (via env vars) and Replit (via connector API).
"""

import os
import httpx
import stripe
from dotenv import load_dotenv
from pathlib import Path

# Load .env for local development
_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(dotenv_path=_ENV_PATH, override=False)

_stripe_initialized = False
_publishable_key = None


def get_stripe_credentials_from_env():
    """Get Stripe credentials from environment variables (local development)."""
    secret_key = os.getenv("STRIPE_SECRET_KEY")
    publishable_key = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
    
    if secret_key:
        return {
            "secret_key": secret_key.strip(),
            "publishable_key": publishable_key.strip() if publishable_key else ""
        }
    return None


def get_stripe_credentials_from_replit():
    """Fetch Stripe credentials from Replit connection API."""
    hostname = os.environ.get("REPLIT_CONNECTORS_HOSTNAME")
    x_replit_token = None
    
    if os.environ.get("REPL_IDENTITY"):
        x_replit_token = "repl " + os.environ["REPL_IDENTITY"]
    elif os.environ.get("WEB_REPL_RENEWAL"):
        x_replit_token = "depl " + os.environ["WEB_REPL_RENEWAL"]
    
    if not x_replit_token or not hostname:
        return None
    
    is_production = os.environ.get("REPLIT_DEPLOYMENT") == "1"
    environment = "production" if is_production else "development"
    
    try:
        url = f"https://{hostname}/api/v2/connection"
        params = {
            "include_secrets": "true",
            "connector_names": "stripe",
            "environment": environment
        }
        headers = {
            "Accept": "application/json",
            "X_REPLIT_TOKEN": x_replit_token
        }
        
        response = httpx.get(url, params=params, headers=headers, timeout=10)
        data = response.json()
        
        connection = data.get("items", [{}])[0]
        settings = connection.get("settings", {})
        
        if settings.get("secret"):
            return {
                "secret_key": settings["secret"],
                "publishable_key": settings.get("publishable", "")
            }
    except Exception as e:
        print(f"[Stripe] Failed to fetch from Replit: {e}")
    
    return None


def get_stripe_credentials():
    """Get Stripe credentials from env vars or Replit API."""
    # Try environment variables first (local development)
    creds = get_stripe_credentials_from_env()
    if creds:
        print("[Stripe] Using credentials from environment variables")
        return creds
    
    # Try Replit connector API
    creds = get_stripe_credentials_from_replit()
    if creds:
        print("[Stripe] Using credentials from Replit")
        return creds
    
    # No credentials found
    raise Exception(
        "Stripe credentials not found. "
        "Set STRIPE_SECRET_KEY in backend/.env for local development, "
        "or configure Stripe in Replit for deployment."
    )


def init_stripe():
    """Initialize Stripe with credentials."""
    global _stripe_initialized, _publishable_key
    
    if _stripe_initialized:
        return
    
    try:
        creds = get_stripe_credentials()
        stripe.api_key = creds["secret_key"]
        _publishable_key = creds["publishable_key"]
        _stripe_initialized = True
        print("[Stripe] Initialized successfully")
    except Exception as e:
        print(f"[Stripe] Failed to initialize: {e}")
        raise


def get_stripe_client():
    """Get initialized Stripe module."""
    init_stripe()
    return stripe


def get_publishable_key():
    """Get Stripe publishable key for frontend."""
    init_stripe()
    return _publishable_key


def get_webhook_secret():
    """Get webhook secret from environment."""
    return os.getenv("STRIPE_WEBHOOK_SECRET")
