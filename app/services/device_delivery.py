"""Fan out a single notification to every device registered for a store.

Browsers receive Web Push (VAPID). Capacitor mobile apps receive FCM. The
caller doesn't need to know which transport handles which device — they
call notify_store() once and we deliver to every channel.
"""
from typing import Optional

from app.services import fcm as fcm_mod
from app.services import push as push_mod


def notify_store(
    supabase,
    store_id: str,
    *,
    title: str,
    body: str,
    url: str = "/",
    user_id: Optional[str] = None,
) -> dict:
    """Deliver a notification to every device registered for `store_id`.

    Pass user_id to scope to a single user (e.g. for /push/test). Returns
    a unified result dict so the caller can log + react uniformly.
    """
    subs_q = (
        supabase.table("push_subscriptions")
        .select("endpoint,p256dh,auth")
        .eq("store_id", store_id)
    )
    fcm_q = supabase.table("fcm_tokens").select("token").eq("store_id", store_id)
    if user_id is not None:
        subs_q = subs_q.eq("user_id", user_id)
        fcm_q = fcm_q.eq("user_id", user_id)

    try:
        subs = (subs_q.execute()).data or []
    except Exception:
        subs = []
    try:
        fcm_rows = (fcm_q.execute()).data or []
    except Exception:
        fcm_rows = []

    tokens = [r.get("token") for r in fcm_rows if r.get("token")]

    webpush_result = (
        push_mod.send_web_push(subs, title=title, body=body, url=url)
        if subs
        else {"sent": 0, "failed": 0, "errors": []}
    )
    fcm_result = (
        fcm_mod.send_fcm(tokens, title=title, body=body, url=url)
        if tokens
        else {"sent": 0, "failed": 0, "invalid_tokens": [], "errors": []}
    )

    if fcm_result.get("invalid_tokens"):
        fcm_mod.purge_invalid_tokens(supabase, fcm_result["invalid_tokens"])

    return {
        "sent": (webpush_result.get("sent", 0) or 0) + (fcm_result.get("sent", 0) or 0),
        "failed": (webpush_result.get("failed", 0) or 0) + (fcm_result.get("failed", 0) or 0),
        "webpush": webpush_result,
        "fcm": fcm_result,
    }
