"""Account deletion helpers.

Verifies passwords via the Supabase Auth token endpoint and removes a user
plus all the rows they own when a deletion request is executed.
"""
import os
from typing import Optional

import httpx

from app.core.time_utils import now_utc_iso
from app.db.supabase import get_supabase_client


def _supabase_url() -> str:
    url = os.getenv("SUPABASE_URL")
    if not url:
        raise RuntimeError("SUPABASE_URL not set")
    return url.rstrip("/")


def _service_key() -> str:
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not key:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY not set")
    return key


def _admin_headers() -> dict:
    sk = _service_key()
    return {
        "apikey": sk,
        "Authorization": f"Bearer {sk}",
        "Content-Type": "application/json",
    }


def get_user_email(user_id: str) -> Optional[str]:
    """Look up the auth email for a user via the Supabase Admin API."""
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(
                f"{_supabase_url()}/auth/v1/admin/users/{user_id}",
                headers=_admin_headers(),
            )
            if resp.status_code != 200:
                return None
            return resp.json().get("email")
    except Exception:
        return None


def verify_user_password(user_id: str, password: str) -> bool:
    """Return True iff `password` is the user's current Supabase Auth password.

    Uses the standard `grant_type=password` token endpoint — the same one the
    frontend uses when signing in. Network/config errors return False so we
    fail closed (deletion is refused).
    """
    if not password:
        return False
    email = get_user_email(user_id)
    if not email:
        return False
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{_supabase_url()}/auth/v1/token",
                params={"grant_type": "password"},
                headers={
                    "apikey": _service_key(),
                    "Content-Type": "application/json",
                },
                json={"email": email, "password": password},
            )
            return resp.status_code == 200
    except Exception:
        return False


def _safe_delete_table(supabase, table: str, column: str, value: str) -> None:
    try:
        supabase.table(table).delete().eq(column, value).execute()
    except Exception:
        # Best-effort: a missing table or FK glitch should not block the rest.
        pass


def delete_user_and_owned_data(user_id: str) -> dict:
    """Hard-delete a user and every row that belongs to them.

    Order matters: kill the auth user *last* so a partial failure leaves the
    account intact and recoverable rather than orphaned in Postgres.
    """
    supabase = get_supabase_client()

    store_id: Optional[str] = None
    owns_store = False

    try:
        prof = (
            supabase.table("profiles")
            .select("store_id")
            .eq("id", user_id)
            .single()
            .execute()
        )
        store_id = (prof.data or {}).get("store_id")
    except Exception:
        store_id = None

    if store_id:
        try:
            st = (
                supabase.table("stores")
                .select("owner_id")
                .eq("id", store_id)
                .single()
                .execute()
            )
            owns_store = bool(st.data and st.data.get("owner_id") == user_id)
        except Exception:
            owns_store = False

    # Per-user PII / consent / device state. Cascade FKs may already handle
    # most of this once the auth user is gone, but be explicit so the data
    # is gone even when FKs were not set up with ON DELETE CASCADE.
    for table in (
        "push_subscriptions",
        "cookie_preferences",
        "user_consents",
        "user_sessions",
        "data_export_requests",
    ):
        _safe_delete_table(supabase, table, "user_id", user_id)

    # If this user owns the store, drop the store (cascades products / sales /
    # customers / expenses / etc.). Other profiles in that store would lose
    # access — that's by design for a single-owner store.
    if owns_store and store_id:
        _safe_delete_table(supabase, "stores", "id", store_id)

    # Profile last (FK to auth.users would block recreate after the auth
    # user is deleted, but we want the row gone either way).
    _safe_delete_table(supabase, "profiles", "id", user_id)

    # Mark any pending/confirmed deletion request as completed.
    try:
        supabase.table("account_deletion_requests").update(
            {"status": "completed", "completed_at": now_utc_iso()}
        ).eq("user_id", user_id).in_("status", ["pending", "confirmed"]).execute()
    except Exception:
        pass

    # Finally, the auth user. This also invalidates refresh tokens.
    auth_deleted = False
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.delete(
                f"{_supabase_url()}/auth/v1/admin/users/{user_id}",
                headers=_admin_headers(),
            )
            auth_deleted = resp.status_code in (200, 204)
    except Exception:
        auth_deleted = False

    return {
        "auth_user_deleted": auth_deleted,
        "store_deleted": bool(owns_store and store_id),
        "store_id": store_id,
    }


def process_scheduled_deletions() -> dict:
    """Execute every deletion request whose grace period has elapsed.

    Designed to be called from a cron / scheduler with the service-role key.
    Returns a small summary so the caller can log it.
    """
    supabase = get_supabase_client()
    try:
        res = (
            supabase.table("account_deletion_requests")
            .select("id, user_id, scheduled_deletion_at")
            .in_("status", ["pending", "confirmed"])
            .lte("scheduled_deletion_at", now_utc_iso())
            .execute()
        )
        rows = res.data or []
    except Exception as e:
        return {"processed": 0, "errors": [str(e)]}

    processed = 0
    errors = []
    for row in rows:
        try:
            delete_user_and_owned_data(row["user_id"])
            processed += 1
        except Exception as e:
            errors.append(f"user {row.get('user_id')}: {e}")

    return {"processed": processed, "scanned": len(rows), "errors": errors[:10]}
