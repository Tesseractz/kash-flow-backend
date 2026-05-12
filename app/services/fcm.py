"""Firebase Cloud Messaging (FCM) delivery for the Capacitor mobile app.

Web Push (app/services/push.py) uses VAPID and only works in real browsers.
On Android/iOS the WebView can't receive Web Push, so we send via FCM
instead — same payload shape, different transport.

Configuration (one of these must be set or get_fcm_app() returns None):
  FIREBASE_SERVICE_ACCOUNT_JSON      — the entire JSON content inline
  FIREBASE_SERVICE_ACCOUNT_JSON_PATH — path to the JSON file on disk
"""
from __future__ import annotations

import json
import os
import threading
from typing import Iterable, List, Optional

_app_lock = threading.Lock()
_app = None  # lazy firebase_admin app
_init_attempted = False
_init_error: Optional[str] = None


def is_fcm_configured() -> bool:
    """True iff the operator provided service-account credentials."""
    return bool(
        os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
        or os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON_PATH")
    )


def get_fcm_app():
    """Return a singleton firebase_admin App or None if not configured.

    Importing firebase_admin lazily keeps it from being a hard dependency
    for the rest of the API — without credentials the FCM endpoints just
    return "not configured" and everything else still boots.
    """
    global _app, _init_attempted, _init_error
    if _app is not None:
        return _app
    with _app_lock:
        if _app is not None:
            return _app
        if _init_attempted:
            return _app

        _init_attempted = True
        if not is_fcm_configured():
            _init_error = "FIREBASE_SERVICE_ACCOUNT_JSON not set"
            return None

        try:
            import firebase_admin
            from firebase_admin import credentials

            inline = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
            path = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON_PATH")
            if inline:
                cred = credentials.Certificate(json.loads(inline))
            elif path:
                cred = credentials.Certificate(path)
            else:
                _init_error = "no credential source"
                return None

            # Use a named app so re-init in tests doesn't blow up.
            try:
                _app = firebase_admin.get_app("kashpoint")
            except ValueError:
                _app = firebase_admin.initialize_app(cred, name="kashpoint")
            return _app
        except Exception as e:  # pragma: no cover — defensive
            _init_error = f"{type(e).__name__}: {e}"
            return None


def reset_for_tests() -> None:
    """Clear cached state. Test-only."""
    global _app, _init_attempted, _init_error
    _app = None
    _init_attempted = False
    _init_error = None


def send_fcm(
    tokens: Iterable[str],
    *,
    title: str,
    body: str,
    url: str = "/",
    data: Optional[dict] = None,
) -> dict:
    """Send a notification to multiple FCM tokens.

    Returns {sent, failed, invalid_tokens, errors}. Invalid tokens are the
    ones FCM reports as UNREGISTERED / INVALID_ARGUMENT — the caller
    should delete those rows so we stop trying.
    """
    token_list = [t for t in tokens if t]
    if not token_list:
        return {"sent": 0, "failed": 0, "invalid_tokens": [], "errors": []}

    app = get_fcm_app()
    if app is None:
        return {
            "sent": 0,
            "failed": len(token_list),
            "invalid_tokens": [],
            "errors": [_init_error or "FCM not configured"],
        }

    try:
        from firebase_admin import messaging
    except Exception as e:  # pragma: no cover
        return {"sent": 0, "failed": len(token_list), "invalid_tokens": [], "errors": [str(e)]}

    payload_data = {"url": url, **(data or {})}
    payload_data = {k: str(v) for k, v in payload_data.items() if v is not None}

    sent = 0
    failed = 0
    invalid: List[str] = []
    errors: List[str] = []

    # send_each handles per-token error reporting without stopping the batch
    # (vs send_multicast which can be opaque). Falls back to send() if the
    # installed firebase-admin doesn't have send_each.
    if hasattr(messaging, "send_each"):
        message = messaging.MulticastMessage(
            tokens=token_list,
            notification=messaging.Notification(title=title, body=body),
            data=payload_data,
        )
        try:
            resp = messaging.send_each_for_multicast(message, app=app)
        except Exception as e:
            return {
                "sent": 0,
                "failed": len(token_list),
                "invalid_tokens": [],
                "errors": [f"{type(e).__name__}: {e}"],
            }
        for i, r in enumerate(resp.responses):
            if r.success:
                sent += 1
            else:
                failed += 1
                err = r.exception
                code = getattr(err, "code", "")
                msg = str(err) if err else "unknown error"
                if code in ("UNREGISTERED", "INVALID_ARGUMENT") or "Requested entity was not found" in msg:
                    invalid.append(token_list[i])
                errors.append(f"token[{i}]: {code or 'error'}: {msg}")
    else:  # pragma: no cover — old firebase-admin
        for t in token_list:
            try:
                messaging.send(
                    messaging.Message(
                        token=t,
                        notification=messaging.Notification(title=title, body=body),
                        data=payload_data,
                    ),
                    app=app,
                )
                sent += 1
            except Exception as e:
                failed += 1
                errors.append(f"{t[:8]}…: {type(e).__name__}: {e}")

    return {
        "sent": sent,
        "failed": failed,
        "invalid_tokens": invalid,
        "errors": errors[:10],
    }


def purge_invalid_tokens(supabase, invalid_tokens: Iterable[str]) -> int:
    """Delete rows whose FCM token has been reported invalid by Google.

    Returns the number we asked to delete (best-effort — Supabase returns
    the deleted rows in `.data`, but we don't depend on that here).
    """
    count = 0
    for tok in invalid_tokens:
        if not tok:
            continue
        try:
            supabase.table("fcm_tokens").delete().eq("token", tok).execute()
            count += 1
        except Exception:
            pass
    return count
