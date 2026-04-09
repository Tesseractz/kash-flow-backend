import os
import json
from typing import Iterable, Optional

from pywebpush import webpush, WebPushException


def get_vapid_public_key() -> Optional[str]:
    return os.getenv("VAPID_PUBLIC_KEY") or None


def _get_vapid_private_key() -> Optional[str]:
    return os.getenv("VAPID_PRIVATE_KEY") or None


def _get_vapid_subject() -> str:
    # RFC8292 suggests "mailto:..." or an https URL identifying sender
    return os.getenv("VAPID_SUBJECT") or "mailto:admin@example.com"


def send_web_push(
    subscriptions: Iterable[dict],
    *,
    title: str,
    body: str,
    url: str = "/",
    icon: Optional[str] = None,
    badge: Optional[str] = None,
) -> dict:
    """
    Send a Web Push notification to multiple subscriptions.

    `subscriptions` should be dicts containing:
      - endpoint
      - p256dh
      - auth
    """
    public_key = get_vapid_public_key()
    private_key = _get_vapid_private_key()
    if not public_key or not private_key:
        return {"sent": 0, "failed": 0, "errors": ["VAPID keys not configured"]}

    payload = {
        "title": title,
        "body": body,
        "url": url,
        **({"icon": icon} if icon else {}),
        **({"badge": badge} if badge else {}),
    }

    sent = 0
    failed = 0
    errors = []

    for sub in subscriptions:
        endpoint = sub.get("endpoint")
        p256dh = sub.get("p256dh")
        auth = sub.get("auth")
        if not endpoint or not p256dh or not auth:
            failed += 1
            errors.append("Invalid subscription shape")
            continue

        subscription_info = {
            "endpoint": endpoint,
            "keys": {"p256dh": p256dh, "auth": auth},
        }

        try:
            webpush(
                subscription_info=subscription_info,
                data=json.dumps(payload),
                vapid_private_key=private_key,
                vapid_claims={"sub": _get_vapid_subject()},
            )
            sent += 1
        except WebPushException as e:
            failed += 1
            try:
                # helpful for debugging (often contains status code)
                errors.append(str(e))
            except Exception:
                errors.append("WebPushException")
        except Exception as e:
            failed += 1
            errors.append(str(e))

    return {"sent": sent, "failed": failed, "errors": errors[:10]}

