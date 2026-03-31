"""
Stripe client for POS backend.
Uses environment variables for credentials.
"""

import os
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


def get_stripe_credentials():
    """Get Stripe credentials from environment variables."""
    creds = get_stripe_credentials_from_env()
    if creds:
        print("[Stripe] Using credentials from environment variables")
        return creds
    
    # No credentials found
    raise Exception(
        "Stripe credentials not found. "
        "Set STRIPE_SECRET_KEY (and optionally STRIPE_PUBLISHABLE_KEY) in your environment."
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
