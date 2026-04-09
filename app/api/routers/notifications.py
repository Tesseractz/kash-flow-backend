from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import app.services.notifications as notifications_mod
import app.services.push as push_mod
import app.services.subscriptions as subscriptions
import app.db.supabase as supabase_client
from app.api.deps import RequestContext, get_current_context
from app.services.notification_settings import fetch_notification_settings
from app.services.reporting import build_daily_report
from app.schemas import PushSubscriptionIn, PushSubscribeResponse
from app.core.time_utils import now_utc_iso

router = APIRouter()


class NotificationStatus(BaseModel):
    email_configured: bool


@router.get("/notifications/status", response_model=NotificationStatus)
def get_notification_status(ctx: RequestContext = Depends(get_current_context)):
    return NotificationStatus(email_configured=notifications_mod.is_email_configured())


@router.get("/push/vapid-public-key")
def push_vapid_public_key(ctx: RequestContext = Depends(get_current_context)):
    key = push_mod.get_vapid_public_key()
    if not key:
        raise HTTPException(status_code=503, detail="VAPID keys not configured")
    return {"public_key": key}


@router.post("/push/subscribe", response_model=PushSubscribeResponse)
def push_subscribe(
    payload: PushSubscriptionIn,
    request: Request,
    ctx: RequestContext = Depends(get_current_context),
):
    supabase = supabase_client.get_supabase_client()
    ua = request.headers.get("user-agent")
    row = {
        "store_id": ctx.store_id,
        "user_id": ctx.user_id,
        "endpoint": payload.endpoint,
        "p256dh": payload.keys.p256dh,
        "auth": payload.keys.auth,
        "user_agent": ua,
        "updated_at": now_utc_iso(),
    }
    try:
        supabase.table("push_subscriptions").upsert(row, on_conflict="endpoint").execute()
        return PushSubscribeResponse(success=True, message="Subscribed")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class PushUnsubscribeRequest(BaseModel):
    endpoint: str


@router.post("/push/unsubscribe", response_model=PushSubscribeResponse)
def push_unsubscribe(payload: PushUnsubscribeRequest, ctx: RequestContext = Depends(get_current_context)):
    supabase = supabase_client.get_supabase_client()
    try:
        supabase.table("push_subscriptions").delete().eq("store_id", ctx.store_id).eq("endpoint", payload.endpoint).execute()
        return PushSubscribeResponse(success=True, message="Unsubscribed")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/push/test")
def push_test(ctx: RequestContext = Depends(get_current_context)):
    supabase = supabase_client.get_supabase_client()
    subs = (
        supabase.table("push_subscriptions")
        .select("endpoint,p256dh,auth")
        .eq("store_id", ctx.store_id)
        .eq("user_id", ctx.user_id)
        .execute()
    ).data or []
    if not subs:
        return {"sent": 0, "message": "No device subscriptions for this user"}
    return push_mod.send_web_push(subs, title="KashPoint test", body="Device notifications are working.", url="/")


class SendLowStockAlertRequest(BaseModel):
    threshold: int = 10
    email: Optional[str] = None
    send_email: bool = False


class NotificationResponse(BaseModel):
    success: bool
    results: List[dict]
    message: str
    payload: Optional[dict] = None


class NotificationSettings(BaseModel):
    notification_email: Optional[str] = None
    low_stock_threshold: int = 10
    daily_summary_enabled: bool = False


@router.get("/notifications/settings", response_model=NotificationSettings)
def get_notification_settings(ctx: RequestContext = Depends(get_current_context)):
    supabase = supabase_client.get_supabase_client()
    settings = fetch_notification_settings(supabase, ctx.store_id)
    return settings or NotificationSettings().model_dump()


@router.put("/notifications/settings", response_model=NotificationSettings)
def update_notification_settings(settings: NotificationSettings, ctx: RequestContext = Depends(get_current_context)):
    supabase = supabase_client.get_supabase_client()
    payload = settings.model_dump()
    payload["store_id"] = ctx.store_id
    try:
        res = supabase.table("notification_settings").upsert(payload, on_conflict="store_id").execute()
        return res.data[0] if res.data else settings.model_dump()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/notifications/low-stock", response_model=NotificationResponse)
def send_low_stock_notification(request: SendLowStockAlertRequest, ctx: RequestContext = Depends(get_current_context)):
    limits = subscriptions.get_store_plan(ctx.store_id)
    if not limits.allow_low_stock_alerts:
        raise HTTPException(status_code=402, detail="Low-stock alerts require Pro or Business plan")

    supabase = supabase_client.get_supabase_client()
    try:
        res = (
            supabase.table("products")
            .select("id,sku,name,quantity")
            .eq("store_id", ctx.store_id)
            .lte("quantity", request.threshold)
            .order("quantity")
            .execute()
        )
        low_stock_products = res.data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not low_stock_products:
        return NotificationResponse(success=True, results=[], message="No products below threshold")

    results = []
    email_to_use = request.email
    if request.send_email and not email_to_use:
        settings = fetch_notification_settings(supabase, ctx.store_id)
        email_to_use = settings.get("notification_email")
        if not email_to_use:
            return NotificationResponse(success=False, results=[], message="Notification email not configured")

    if request.send_email and email_to_use:
        subject, html_body = notifications_mod.generate_low_stock_email(low_stock_products)
        result = notifications_mod.send_email(email_to_use, subject, html_body)
        results.append(result.model_dump())

    all_success = all(r.get("success", False) for r in results) if results else True

    return NotificationResponse(
        success=all_success,
        results=results,
        message=f"Processed notifications for {len(low_stock_products)} low-stock products",
    )


class DailySummaryRequest(BaseModel):
    date_utc: Optional[str] = None
    email: Optional[str] = None
    send_email: bool = False


@router.post("/notifications/daily-summary", response_model=NotificationResponse)
def send_daily_summary_notification(request: DailySummaryRequest, ctx: RequestContext = Depends(get_current_context)):
    target_date, totals, _transactions = build_daily_report(ctx, request.date_utc)
    date_label = target_date.strftime("%Y-%m-%d")
    summary_payload = {
        "date_label": date_label,
        "totals": totals,
    }

    results = []
    supabase = supabase_client.get_supabase_client()
    email_to_use = request.email
    if request.send_email and not email_to_use:
        settings = fetch_notification_settings(supabase, ctx.store_id)
        if settings.get("daily_summary_enabled"):
            email_to_use = settings.get("notification_email")
        if not email_to_use:
            return NotificationResponse(
                success=False,
                results=[],
                payload=summary_payload,
                message="Notification email not configured",
            )

    if request.send_email and email_to_use:
        store_name = "KashPoint"
        try:
            store_res = supabase.table("stores").select("name").eq("id", ctx.store_id).single().execute()
            store_name = store_res.data.get("name") or store_name
        except Exception:
            pass
        subject, html_body = notifications_mod.generate_daily_summary_email(
            {"date_label": date_label, "totals": totals},
            store_name=store_name,
        )
        result = notifications_mod.send_email(email_to_use, subject, html_body)
        results.append(result.model_dump())

    all_success = all(r.get("success", False) for r in results) if results else True

    return NotificationResponse(
        success=all_success,
        results=results,
        payload=summary_payload,
        message="Daily summary processed",
    )


@router.post("/receipts/send", response_model=NotificationResponse)
def send_receipt(request: notifications_mod.ReceiptRequest, ctx: RequestContext = Depends(get_current_context)):
    supabase = supabase_client.get_supabase_client()
    try:
        sale_res = (
            supabase.table("sales")
            .select("*")
            .eq("id", request.sale_id)
            .eq("store_id", ctx.store_id)
            .single()
            .execute()
        )
        sale = sale_res.data
        if not sale:
            raise HTTPException(status_code=404, detail="Sale not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    try:
        product_res = supabase.table("products").select("id,name,price").eq("id", sale["product_id"]).single().execute()
        product = product_res.data or {}
    except Exception:
        product = {"name": f"Product #{sale['product_id']}", "price": 0}

    total_price = float(sale.get("total_price", 0))
    sale_data = {
        "id": sale["id"],
        "timestamp": sale.get("timestamp", ""),
        "total": total_price,
        "item_count": sale.get("quantity_sold", 0),
        "items": [
            {
                "name": product.get("name", "Product"),
                "quantity": sale.get("quantity_sold", 1),
                "price": float(product.get("price", 0)),
                "total": total_price,
            }
        ],
        "payment_method": request.payment_method or "cash",
        "payment_amount": request.payment_amount or total_price,
        "change": request.change_amount or 0,
    }

    results = []

    if request.send_email and request.customer_email:
        html_body = notifications_mod.generate_receipt_html(sale_data)
        result = notifications_mod.send_email(
            request.customer_email,
            f"Receipt #{sale['id']} - KashPoint",
            html_body,
        )
        results.append(result.model_dump())

    all_success = all(r.get("success", False) for r in results) if results else True

    return NotificationResponse(
        success=all_success,
        results=results,
        message="Receipt sent" if all_success else "Failed to send receipt",
    )
