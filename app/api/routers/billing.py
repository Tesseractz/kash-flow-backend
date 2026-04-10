import json
import os
from typing import Literal, Optional
from urllib.parse import unquote_plus, urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

import app.clients.paystack as paystack_client
import app.db.supabase as supabase_client
from app.api.deps import RequestContext, get_current_context
from app.core.http_config import normalize_frontend_base, resolve_frontend_base_url
from app.core.time_utils import now_utc_iso

router = APIRouter()


class CheckoutRequest(BaseModel):
    plan: Literal["pro"]
    email: Optional[str] = None


class PaystackSyncRequest(BaseModel):
    reference: str


def _ensure_trial_consumed_once(supa, store_id, update: dict) -> None:
    """First successful Paystack payment/subscription for this store consumes the one-time trial."""
    try:
        r = supa.table("subscriptions").select("trial_consumed_at").eq("store_id", store_id).single().execute()
        data = getattr(r, "data", None) or {}
        if isinstance(data, dict) and data.get("trial_consumed_at"):
            return
    except Exception:
        pass
    update["trial_consumed_at"] = now_utc_iso()


def _webhook_next_payment_iso(data: dict) -> Optional[str]:
    """Paystack next charge date for subscription webhooks / charge.success payloads."""
    sub = data.get("subscription")
    if isinstance(sub, dict) and sub.get("next_payment_date"):
        return sub.get("next_payment_date")
    if data.get("next_payment_date"):
        return data.get("next_payment_date")
    sc = data.get("subscription_code")
    if isinstance(sub, dict):
        sc = sc or sub.get("subscription_code")
    if sc:
        details = paystack_client.fetch_subscription_by_code(sc)
        if details:
            return details.get("next_payment_date")
    return None


@router.get("/billing")
def redirect_paystack_return_to_spa(request: Request):
    q = request.url.query
    suffix = f"?{q}" if q else ""
    base = normalize_frontend_base(os.getenv("FRONTEND_URL", "http://localhost:5000"))
    if not base:
        base = "http://localhost:5000"
    location = f"{base}/billing{suffix}"
    loc_host = (urlparse(location).netloc or "").lower()
    req_host = (request.url.netloc or "").lower()
    if loc_host == req_host:
        h = (request.url.hostname or "").lower()
        if h in ("localhost", "127.0.0.1", "::1"):
            location = f"http://localhost:5000/billing{suffix}"
        else:
            raise HTTPException(
                status_code=500,
                detail="FRONTEND_URL must be the SPA origin (not this API). Set FRONTEND_URL to your frontend URL.",
            )
    return RedirectResponse(url=location, status_code=307)


@router.get("/billing/config")
def get_billing_config():
    no_trial = (paystack_client.get_paystack_plan_code_no_trial() or "").strip()
    return {
        "provider": "paystack",
        "paystack": {
            "public_key": paystack_client.get_paystack_public_key(),
            "plan_code": paystack_client.get_paystack_plan_code(),
            "plan_code_no_trial_configured": bool(no_trial),
            "currency": "ZAR",
        },
    }


@router.post("/billing/checkout")
def create_checkout_session(
    body: CheckoutRequest,
    request: Request,
    ctx: RequestContext = Depends(get_current_context),
):
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")

    email = (getattr(body, "email", None) or "").strip()
    if not email:
        raise HTTPException(status_code=400, detail="Missing email for Paystack checkout")

    supa = supabase_client.get_supabase_client()
    trial_consumed = False
    try:
        sub_row = supa.table("subscriptions").select("trial_consumed_at").eq("store_id", ctx.store_id).single().execute()
        data = getattr(sub_row, "data", None) or {}
        if isinstance(data, dict):
            trial_consumed = bool(data.get("trial_consumed_at"))
    except Exception:
        trial_consumed = False

    plan_code: Optional[str] = None
    if trial_consumed:
        nt = (paystack_client.get_paystack_plan_code_no_trial() or "").strip()
        if not nt:
            raise HTTPException(
                status_code=400,
                detail="This store has already used its one-time trial. Add PAYSTACK_PLAN_CODE_NO_TRIAL "
                "(duplicate of your Pro plan without a trial period) to the server environment.",
            )
        plan_code = nt

    frontend_url = resolve_frontend_base_url(request)
    callback_url = f"{frontend_url}/billing?success=1"
    amount_kobo = 25000

    url = paystack_client.initialize_transaction(
        email=email,
        amount_kobo=amount_kobo,
        callback_url=callback_url,
        metadata={"store_id": str(ctx.store_id), "plan": body.plan},
        plan_code=plan_code,
    )
    try:
        supa.table("subscriptions").upsert(
            {
                "store_id": ctx.store_id,
                "billing_provider": "paystack",
            }
        ).execute()
    except Exception:
        pass
    return {"url": url}


@router.post("/billing/paystack/sync")
def paystack_sync_after_checkout(body: PaystackSyncRequest, ctx: RequestContext = Depends(get_current_context)):
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")

    try:
        pdata = paystack_client.verify_transaction(reference=body.reference)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    meta = pdata.get("metadata") or {}
    store_id = meta.get("store_id")
    if not store_id or str(store_id) != str(ctx.store_id):
        raise HTTPException(status_code=403, detail="This payment does not belong to your store.")

    plan = meta.get("plan") or "pro"
    if plan == "business":
        plan = "pro"

    if pdata.get("status") != "success":
        raise HTTPException(status_code=400, detail="Transaction is not successful")

    cust_code = None
    cust = pdata.get("customer")
    if isinstance(cust, dict):
        cust_code = cust.get("customer_code")

    sub_code, email_tok, next_payment_iso = paystack_client.find_subscription_for_customer(pdata, cust_code)

    update = {
        "store_id": ctx.store_id,
        "billing_provider": "paystack",
        "plan": plan,
        "status": "active",
    }
    if cust_code:
        update["paystack_customer_code"] = cust_code
    if sub_code:
        update["paystack_subscription_code"] = sub_code
    if email_tok:
        update["paystack_email_token"] = email_tok
    # Replace stale period end (e.g. after cancel + resubscribe) with Paystack's next charge date.
    if sub_code:
        update["current_period_end"] = next_payment_iso

    supa = supabase_client.get_supabase_client()
    _ensure_trial_consumed_once(supa, ctx.store_id, update)
    supa.table("subscriptions").upsert(update).execute()
    return {"synced": True, "has_subscription": bool(sub_code)}


@router.post("/billing/cancel")
def cancel_subscription(ctx: RequestContext = Depends(get_current_context)):
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    supa = supabase_client.get_supabase_client()

    try:
        sub_res = supa.table("subscriptions").select("*").eq("store_id", ctx.store_id).single().execute()
        sub = sub_res.data or {}
    except Exception:
        sub = {}

    if not sub:
        raise HTTPException(status_code=400, detail="No subscription found for this store.")

    # If the user is on a trial (no Paystack subscription yet), allow canceling locally.
    status = (sub.get("status") or "").lower()
    if status == "trialing" and not sub.get("paystack_subscription_code"):
        supa.table("subscriptions").update(
            {
                "billing_provider": "paystack",
                "status": "canceled",
                "plan": "expired",
                "current_period_end": None,
            }
        ).eq("store_id", ctx.store_id).execute()
        return {"canceled": True, "note": "Trial canceled locally (no Paystack subscription to disable)."}

    code = sub.get("paystack_subscription_code")
    token = sub.get("paystack_email_token")

    if not code or not token:
        cust_code = sub.get("paystack_customer_code")
        if cust_code:
            found_code, found_token, _ = paystack_client.find_subscription_for_customer({}, cust_code)
            if found_code and found_token:
                code, token = found_code, found_token
                supa.table("subscriptions").update(
                    {"paystack_subscription_code": code, "paystack_email_token": token}
                ).eq("store_id", ctx.store_id).execute()

    if not code or not token:
        # We don't have enough Paystack identifiers to disable billing via API.
        # Still allow the user to cancel access locally so the app state is consistent.
        supa.table("subscriptions").update(
            {
                "billing_provider": "paystack",
                "status": "canceled",
                "plan": "expired",
                "current_period_end": None,
            }
        ).eq("store_id", ctx.store_id).execute()
        return {
            "canceled": True,
            "warning": "No Paystack subscription identifiers found for this store. Your plan was canceled in-app, but if Paystack is billing you, you must cancel from your Paystack dashboard or Paystack subscription emails.",
        }

    try:
        paystack_client.disable_subscription(subscription_code=code, email_token=token)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    supa.table("subscriptions").update(
        {
            "billing_provider": "paystack",
            "status": "canceled",
            "plan": "expired",
            "current_period_end": None,
        }
    ).eq("store_id", ctx.store_id).execute()
    return {"canceled": True}


@router.post("/paystack/webhook")
async def paystack_webhook(request: Request):
    raw = await request.body()
    signature = request.headers.get("x-paystack-signature")
    if not paystack_client.verify_paystack_signature(raw, signature):
        raise HTTPException(status_code=400, detail="Invalid Paystack signature")

    try:
        try:
            payload = await request.json()
        except Exception:
            try:
                payload = json.loads(raw)
            except Exception:
                if isinstance(raw, (bytes, bytearray)):
                    s = raw.decode("utf-8", errors="ignore")
                else:
                    s = str(raw)
                if s.startswith("payload="):
                    payload = json.loads(unquote_plus(s[len("payload=") :]))
                else:
                    raise
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_type = payload.get("event")
    data = payload.get("data") or {}
    meta = data.get("metadata") or {}
    store_id = meta.get("store_id")
    plan = meta.get("plan") or "pro"

    supa = supabase_client.get_supabase_client()

    event_id = data.get("id") or payload.get("id")
    if event_id:
        try:
            supa.table("webhook_events").insert(
                {
                    "id": f"paystack:{event_id}",
                    "type": f"paystack:{event_type}",
                    "received_at": now_utc_iso(),
                }
            ).execute()
        except Exception as e:
            msg = str(e).lower()
            if "duplicate" in msg or "unique" in msg or "23505" in msg:
                return {"received": True, "duplicate": True}
            raise

    if not store_id:
        return {"received": True}

    update = {
        "store_id": store_id,
        "billing_provider": "paystack",
        "plan": "pro" if plan == "business" else plan,
    }

    if event_type == "charge.success":
        update["status"] = "active"
        cust = data.get("customer") or {}
        if isinstance(cust, dict):
            update["paystack_customer_code"] = cust.get("customer_code")
        sub_code = data.get("subscription_code")
        email_tok = data.get("email_token")
        sub = data.get("subscription") or {}
        if isinstance(sub, dict):
            sub_code = sub_code or sub.get("subscription_code")
            email_tok = email_tok or sub.get("email_token")
        if sub_code:
            update["paystack_subscription_code"] = sub_code
        if email_tok:
            update["paystack_email_token"] = email_tok
        if update.get("paystack_subscription_code"):
            update["current_period_end"] = _webhook_next_payment_iso(data)
        _ensure_trial_consumed_once(supa, store_id, update)
        supa.table("subscriptions").upsert(update).execute()

    elif event_type in ("subscription.create", "subscription.enable"):
        update["status"] = "active"
        update["paystack_subscription_code"] = data.get("subscription_code")
        update["paystack_email_token"] = data.get("email_token")
        npd = data.get("next_payment_date")
        if not npd and isinstance(data.get("subscription"), dict):
            npd = data["subscription"].get("next_payment_date")
        sc = update.get("paystack_subscription_code")
        if sc and not npd:
            details = paystack_client.fetch_subscription_by_code(sc)
            npd = details.get("next_payment_date") if details else None
        if sc:
            update["current_period_end"] = npd
        _ensure_trial_consumed_once(supa, store_id, update)
        supa.table("subscriptions").upsert(update).execute()

    elif event_type in ("subscription.disable", "subscription.not_renew"):
        supa.table("subscriptions").update(
            {
                "billing_provider": "paystack",
                "status": "canceled",
                "plan": "expired",
                "current_period_end": None,
            }
        ).eq("store_id", store_id).execute()

    elif event_type in ("invoice.payment_failed",):
        supa.table("subscriptions").update(
            {
                "billing_provider": "paystack",
                "status": "past_due",
                "plan": update["plan"],
            }
        ).eq("store_id", store_id).execute()

    return {"received": True}
