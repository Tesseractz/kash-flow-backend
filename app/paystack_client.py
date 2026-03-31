import os
import hashlib
import hmac
from typing import Optional

import httpx


def _mode() -> str:
    return (os.getenv("PAYSTACK_MODE") or "live").strip().lower()


def _get(key_base: str) -> str:
    """
    Resolve secrets with optional PAYSTACK_MODE switching.
    Priority:
      1) PAYSTACK_<KEY_BASE>_<MODE>
      2) PAYSTACK_<KEY_BASE>
    """
    mode = _mode()
    v = os.getenv(f"PAYSTACK_{key_base}_{mode.upper()}")
    if v:
        return v.strip()
    v = os.getenv(f"PAYSTACK_{key_base}")
    return (v or "").strip()


def get_paystack_secret_key() -> str:
    return _get("SECRET_KEY")


def get_paystack_public_key() -> str:
    return _get("PUBLIC_KEY")


def get_paystack_plan_code() -> str:
    return _get("PLAN_CODE")


def verify_paystack_signature(raw_body: bytes, signature: Optional[str]) -> bool:
    secret = get_paystack_secret_key()
    if not secret or not signature:
        return False
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(digest, signature)


def initialize_transaction(*, email: str, amount_kobo: int, callback_url: str, metadata: dict) -> str:
    """
    Create a Paystack hosted payment page (transaction initialize).
    Returns authorization_url.
    """
    secret = get_paystack_secret_key()
    if not secret:
        raise Exception("Missing PAYSTACK_SECRET_KEY")

    plan_code = get_paystack_plan_code()
    if not plan_code:
        raise Exception("Missing PAYSTACK_PLAN_CODE")

    resp = httpx.post(
        "https://api.paystack.co/transaction/initialize",
        headers={
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
        },
        json={
            "email": email,
            "amount": int(amount_kobo),
            "plan": plan_code,
            "callback_url": callback_url,
            "metadata": metadata,
        },
        timeout=15.0,
    )
    data = resp.json() if resp.content else {}
    if resp.status_code >= 400 or not data.get("status"):
        raise Exception(data.get("message") or f"Paystack init failed: HTTP {resp.status_code}")
    return data["data"]["authorization_url"]

