import app.db.supabase as supabase_client
import app.services.subscriptions as subscriptions
from app.core.time_utils import now_utc_iso


def log_audit_event(
    store_id: str,
    user_id: str,
    action: str,
    resource_type: str,
    resource_id: str = None,
    details: str = None,
):
    try:
        supabase = supabase_client.get_supabase_client()
        limits = subscriptions.get_store_plan(store_id)
        if limits.allow_audit_logs:
            supabase.table("audit_logs").insert(
                {
                    "store_id": store_id,
                    "user_id": user_id,
                    "action": action,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "details": details,
                    "timestamp": now_utc_iso(),
                }
            ).execute()
    except Exception:
        pass
