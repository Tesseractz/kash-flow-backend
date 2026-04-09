from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request

import app.services.audit_log as audit_log
import app.db.supabase as supabase_client
from app.api.deps import RequestContext, get_current_context
from app.schemas import (
    AccountDeletionCreate,
    AccountDeletionRequest,
    ConsentUpdate,
    CookiePreferences,
    DataExportRequest,
    PrivacySettings,
    PrivacySettingsUpdate,
    UserConsent,
    UserSession,
)
from app.core.time_utils import now_utc_iso

router = APIRouter()


@router.get("/privacy/consents", response_model=List[UserConsent])
def get_user_consents(request: Request, ctx: RequestContext = Depends(get_current_context)):
    supabase = supabase_client.get_supabase_client()
    res = supabase.table("user_consents").select("*").eq("user_id", ctx.user_id).execute()
    return res.data or []


@router.post("/privacy/consents", response_model=UserConsent)
def update_consent(consent: ConsentUpdate, request: Request, ctx: RequestContext = Depends(get_current_context)):
    supabase = supabase_client.get_supabase_client()

    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent", "")

    consent_data = {
        "user_id": ctx.user_id,
        "consent_type": consent.consent_type,
        "consented": consent.consented,
        "consent_version": consent.consent_version,
        "ip_address": ip_address,
        "user_agent": user_agent,
        "consented_at": now_utc_iso() if consent.consented else None,
        "revoked_at": now_utc_iso() if not consent.consented else None,
    }

    try:
        res = supabase.table("user_consents").upsert(consent_data, on_conflict="user_id,consent_type").execute()
        audit_log.log_audit_event(
            ctx.store_id,
            ctx.user_id,
            "consent_update",
            "user_consent",
            consent.consent_type,
            f"{'Granted' if consent.consented else 'Revoked'} consent",
        )
        return res.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _get_privacy_settings_data(ctx: RequestContext) -> PrivacySettings:
    supabase = supabase_client.get_supabase_client()
    res = (
        supabase.table("profiles")
        .select("marketing_emails_enabled, push_notifications_enabled, data_analytics_enabled, two_factor_enabled")
        .eq("id", ctx.user_id)
        .single()
        .execute()
    )

    if not res.data:
        return PrivacySettings()

    return PrivacySettings(
        marketing_emails_enabled=res.data.get("marketing_emails_enabled", False),
        push_notifications_enabled=res.data.get("push_notifications_enabled", False),
        data_analytics_enabled=res.data.get("data_analytics_enabled", True),
        two_factor_enabled=res.data.get("two_factor_enabled", False),
    )


@router.get("/privacy/settings", response_model=PrivacySettings)
def get_privacy_settings(ctx: RequestContext = Depends(get_current_context)):
    return _get_privacy_settings_data(ctx)


@router.put("/privacy/settings", response_model=PrivacySettings)
def update_privacy_settings(settings: PrivacySettingsUpdate, ctx: RequestContext = Depends(get_current_context)):
    supabase = supabase_client.get_supabase_client()
    update_data = settings.model_dump(exclude_unset=True)

    try:
        supabase.table("profiles").update(update_data).eq("id", ctx.user_id).execute()
        audit_log.log_audit_event(
            ctx.store_id,
            ctx.user_id,
            "update",
            "privacy_settings",
            ctx.user_id,
            f"Updated settings: {list(update_data.keys())}",
        )

        return _get_privacy_settings_data(ctx)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/privacy/sessions", response_model=List[UserSession])
def get_user_sessions(ctx: RequestContext = Depends(get_current_context)):
    supabase = supabase_client.get_supabase_client()
    res = (
        supabase.table("user_sessions")
        .select("*")
        .eq("user_id", ctx.user_id)
        .order("last_active_at", desc=True)
        .execute()
    )
    return res.data or []


@router.delete("/privacy/sessions/{session_id}", status_code=204)
def revoke_session(session_id: str, ctx: RequestContext = Depends(get_current_context)):
    supabase = supabase_client.get_supabase_client()
    try:
        supabase.table("user_sessions").delete().eq("id", session_id).eq("user_id", ctx.user_id).execute()
        audit_log.log_audit_event(ctx.store_id, ctx.user_id, "revoke", "session", session_id, "Revoked session")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return None


@router.delete("/privacy/sessions", status_code=204)
def revoke_all_sessions(ctx: RequestContext = Depends(get_current_context)):
    supabase = supabase_client.get_supabase_client()
    try:
        supabase.table("user_sessions").delete().eq("user_id", ctx.user_id).eq("is_current", False).execute()
        audit_log.log_audit_event(
            ctx.store_id, ctx.user_id, "revoke_all", "session", ctx.user_id, "Revoked all other sessions"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return None


@router.post("/privacy/data-export", response_model=DataExportRequest, status_code=201)
def request_data_export(ctx: RequestContext = Depends(get_current_context)):
    supabase = supabase_client.get_supabase_client()

    pending = supabase.table("data_export_requests").select("*").eq("user_id", ctx.user_id).eq("status", "pending").execute()
    if pending.data:
        raise HTTPException(status_code=400, detail="You already have a pending data export request")

    try:
        res = (
            supabase.table("data_export_requests")
            .insert(
                {
                    "user_id": ctx.user_id,
                    "status": "pending",
                    "requested_at": now_utc_iso(),
                }
            )
            .execute()
        )
        audit_log.log_audit_event(
            ctx.store_id, ctx.user_id, "request", "data_export", res.data[0]["id"], "Requested data export"
        )
        return res.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/privacy/data-export", response_model=List[DataExportRequest])
def get_data_export_requests(ctx: RequestContext = Depends(get_current_context)):
    supabase = supabase_client.get_supabase_client()
    res = (
        supabase.table("data_export_requests")
        .select("*")
        .eq("user_id", ctx.user_id)
        .order("requested_at", desc=True)
        .execute()
    )
    return res.data or []


@router.post("/privacy/delete-account", response_model=AccountDeletionRequest, status_code=201)
def request_account_deletion(request_data: AccountDeletionCreate, ctx: RequestContext = Depends(get_current_context)):
    supabase = supabase_client.get_supabase_client()

    pending = (
        supabase.table("account_deletion_requests")
        .select("*")
        .eq("user_id", ctx.user_id)
        .in_("status", ["pending", "confirmed"])
        .execute()
    )
    if pending.data:
        raise HTTPException(status_code=400, detail="You already have a pending deletion request")

    scheduled_date = datetime.now(timezone.utc) + timedelta(days=30)

    try:
        res = (
            supabase.table("account_deletion_requests")
            .insert(
                {
                    "user_id": ctx.user_id,
                    "reason": request_data.reason,
                    "status": "pending",
                    "requested_at": now_utc_iso(),
                    "scheduled_deletion_at": scheduled_date.isoformat(),
                }
            )
            .execute()
        )
        audit_log.log_audit_event(
            ctx.store_id, ctx.user_id, "request", "account_deletion", res.data[0]["id"], "Requested account deletion"
        )
        return res.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/privacy/delete-account/{request_id}", status_code=204)
def cancel_account_deletion(request_id: str, ctx: RequestContext = Depends(get_current_context)):
    supabase = supabase_client.get_supabase_client()

    try:
        res = (
            supabase.table("account_deletion_requests")
            .update({"status": "cancelled"})
            .eq("id", request_id)
            .eq("user_id", ctx.user_id)
            .in_("status", ["pending", "confirmed"])
            .execute()
        )

        if not res.data:
            raise HTTPException(status_code=404, detail="Deletion request not found or already processed")

        audit_log.log_audit_event(
            ctx.store_id, ctx.user_id, "cancel", "account_deletion", request_id, "Cancelled account deletion"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return None


@router.post("/privacy/cookies", response_model=CookiePreferences)
def save_cookie_preferences(preferences: CookiePreferences, request: Request, ctx: RequestContext = Depends(get_current_context)):
    supabase = supabase_client.get_supabase_client()
    ip_address = request.client.host if request.client else None

    prefs_data = {
        "user_id": ctx.user_id,
        "essential": True,
        "analytics": preferences.analytics,
        "marketing": preferences.marketing,
        "functional": preferences.functional,
        "ip_address": ip_address,
        "consented_at": now_utc_iso(),
    }

    try:
        res = supabase.table("cookie_preferences").upsert(prefs_data, on_conflict="user_id").execute()
        return CookiePreferences(**res.data[0])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/privacy/cookies", response_model=CookiePreferences)
def get_cookie_preferences(ctx: RequestContext = Depends(get_current_context)):
    supabase = supabase_client.get_supabase_client()
    res = supabase.table("cookie_preferences").select("*").eq("user_id", ctx.user_id).single().execute()

    if not res.data:
        return CookiePreferences()

    return CookiePreferences(
        essential=True,
        analytics=res.data.get("analytics", False),
        marketing=res.data.get("marketing", False),
        functional=res.data.get("functional", True),
    )
