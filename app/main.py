import os
from datetime import datetime, timezone, date
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Response, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from pathlib import Path

# Load backend/.env early so all modules (auth, clients) see variables on import,
# even when server is started from a different working directory.
_ENV_PATH = (Path(__file__).resolve().parents[1] / ".env")
load_dotenv(dotenv_path=_ENV_PATH, override=False)

from .supabase_client import get_supabase_client
from .schemas import (
    Product,
    ProductCreate,
    ProductUpdate,
    Sale,
    SaleCreate,
    ReportResponse,
    ReportTotals,
)
from .deps import get_current_context, RequestContext
from .subscriptions import enforce_limits_on_create_product, get_store_plan, get_plan_info
from .stripe_client import get_stripe_client, get_publishable_key
import csv
import io

app = FastAPI(title="Kash-Flow API", version="1.0.0")

# CORS
cors_origins_env = os.getenv("BACKEND_CORS_ORIGINS", "")
allowed_origins = (
    [o.strip() for o in cors_origins_env.split(",") if o.strip()]
    if cors_origins_env
    else ["*"]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Products
@app.get("/products", response_model=List[Product])
def list_products(
    response: Response,
    q: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    page: Optional[int] = None,
    page_size: Optional[int] = None,
    ctx: RequestContext = Depends(get_current_context),
):
    supabase = get_supabase_client()
    try:
        query = supabase.table("products").select("*").eq("store_id", ctx.store_id)
        if q:
            # match name or sku (string), and id if numeric
            conds = [f"name.ilike.%{q}%", f"sku.ilike.%{q}%"]
            if q.isdigit():
                conds.append(f"id.eq.{int(q)}")
            query = query.or_(",".join(conds))
        if min_price is not None:
            query = query.gte("price", min_price)
        if max_price is not None:
            query = query.lte("price", max_price)

        query = query.order("id")

        # pagination
        total = None
        if page and page_size:
            # compute total count (simple exact count via separate query)
            count_q = supabase.table("products").select("id").eq("store_id", ctx.store_id)
            if q:
                count_q = count_q.ilike("name", f"%{q}%")
                if q.isdigit():
                    count_q = count_q.or_(f"id.eq.{int(q)}")
            if min_price is not None:
                count_q = count_q.gte("price", min_price)
            if max_price is not None:
                count_q = count_q.lte("price", max_price)
            count_res = count_q.execute()
            total = len(count_res.data or [])

            start = (page - 1) * page_size
            end = start + page_size - 1
            query = query.range(start, end)

        res = query.execute()
        if total is not None:
            response.headers["X-Total-Count"] = str(total)
        return res.data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/products", response_model=Product, status_code=201)
def create_product(payload: ProductCreate, ctx: RequestContext = Depends(get_current_context)):
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    enforce_limits_on_create_product(ctx.store_id)
    supabase = get_supabase_client()
    try:
        res = (
            supabase.table("products")
            .insert(
                {
                    **({"id": payload.id} if payload.id is not None else {}),
                    **({"sku": payload.sku} if payload.sku else {}),
                    "name": payload.name,
                    "price": payload.price,
                    "quantity": payload.quantity,
                    "cost_price": payload.cost_price if payload.cost_price is not None else 0,
                    **({"image_url": payload.image_url} if payload.image_url else {}),
                    "store_id": ctx.store_id,
                }
            )
            .execute()
        )
        product = None
        if isinstance(res.data, list) and len(res.data) > 0:
            product = res.data[0]
        else:
            fetch = (
                supabase.table("products")
                .select("*")
                .eq("store_id", ctx.store_id)
                .eq("name", payload.name)
                .eq("price", payload.price)
                .eq("quantity", payload.quantity)
                .order("id", desc=True)
                .limit(1)
                .execute()
            )
            if isinstance(fetch.data, list) and len(fetch.data) > 0:
                product = fetch.data[0]
        
        if product:
            log_audit_event(ctx.store_id, ctx.user_id, "create", "product", str(product["id"]), f"Created product: {payload.name}")
            return product
        raise HTTPException(status_code=500, detail="Insert succeeded but no data returned")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/products/{product_id}", response_model=Product)
def update_product(product_id: int, payload: ProductUpdate, ctx: RequestContext = Depends(get_current_context)):
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    supabase = get_supabase_client()
    update_data = {k: v for k, v in payload.model_dump(exclude_unset=True).items()}
    if not update_data:
        # nothing to update, return current
        try:
            existing = (
                supabase.table("products").select("*").eq("id", product_id).eq("store_id", ctx.store_id).single().execute()
            )
            if not existing.data:
                raise HTTPException(status_code=404, detail="Product not found")
            return existing.data
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=404, detail="Product not found")

    try:
        res = (
            supabase.table("products")
            .update(update_data)
            .eq("id", product_id)
            .eq("store_id", ctx.store_id)
            .execute()
        )
        product = None
        if isinstance(res.data, list) and len(res.data) > 0:
            product = res.data[0]
        else:
            existing = supabase.table("products").select("*").eq("id", product_id).eq("store_id", ctx.store_id).single().execute()
            if not existing.data:
                raise HTTPException(status_code=404, detail="Product not found")
            product = existing.data
        
        log_audit_event(ctx.store_id, ctx.user_id, "update", "product", str(product_id), f"Updated product fields: {list(update_data.keys())}")
        return product
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/products/{product_id}", status_code=204)
def delete_product(product_id: int, ctx: RequestContext = Depends(get_current_context)):
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    supabase = get_supabase_client()
    try:
        supabase.table("products").delete().eq("id", product_id).eq("store_id", ctx.store_id).execute()
        log_audit_event(ctx.store_id, ctx.user_id, "delete", "product", str(product_id), "Deleted product")
        return None
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Sales
@app.get("/sales", response_model=List[Sale])
def list_sales(ctx: RequestContext = Depends(get_current_context)):
    supabase = get_supabase_client()
    try:
        res = supabase.table("sales").select("*").eq("store_id", ctx.store_id).order("timestamp", desc=True).execute()
        return res.data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/sales", response_model=Sale, status_code=201)
def create_sale(payload: SaleCreate, ctx: RequestContext = Depends(get_current_context)):
    """
    Process a sale transactionally in the database.
    """
    supabase = get_supabase_client()
    try:
        rpc = supabase.rpc("process_sale", {
            "p_store_id": ctx.store_id,
            "p_product_id": payload.product_id,
            "p_qty": payload.quantity_sold,
            "p_sold_by": ctx.user_id
        }).execute()
        sale = None
        if isinstance(rpc.data, list) and len(rpc.data) > 0:
            sale = rpc.data[0]
        elif isinstance(rpc.data, dict):
            sale = rpc.data
        
        if sale:
            log_audit_event(ctx.store_id, ctx.user_id, "create", "sale", str(sale.get("id", "")), f"Sale: product {payload.product_id}, qty {payload.quantity_sold}")
            return sale
        else:
            raise HTTPException(status_code=500, detail="Sale failed")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# Reports
def _build_daily_report(ctx: RequestContext, date_utc: Optional[str] = None):
    """
    Returns (target_date, totals, transactions) for the given UTC date (YYYY-MM-DD).
    """
    supabase = get_supabase_client()
    if date_utc:
        try:
            target_date = datetime.fromisoformat(date_utc).date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    else:
        target_date = datetime.now(timezone.utc).date()

    day_start = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc).isoformat()
    day_end = datetime.combine(target_date, datetime.max.time(), tzinfo=timezone.utc).isoformat()

    try:
        sales_res = (
            supabase.table("sales")
            .select("*")
            .eq("store_id", ctx.store_id)
            .gte("timestamp", day_start)
            .lte("timestamp", day_end)
            .order("timestamp", desc=True)
            .execute()
        )
        transactions = sales_res.data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    total_sales_count = len(transactions)
    total_revenue = float(sum(float(tx["total_price"]) for tx in transactions))

    # Compute profit using current product cost_price (fallback 0 if absent)
    try:
        products_res = supabase.table("products").select("id,cost_price").eq("store_id", ctx.store_id).execute()
        cost_by_id = {int(p["id"]): float(p.get("cost_price", 0) or 0) for p in (products_res.data or [])}
    except Exception:
        cost_by_id = {}
    total_profit = 0.0
    for tx in transactions:
        pid = int(tx["product_id"])
        qty = int(tx["quantity_sold"])
        cost = cost_by_id.get(pid, 0.0)
        total_profit += float(tx["total_price"]) - (cost * qty)

    totals = {
        "total_sales_count": total_sales_count,
        "total_revenue": total_revenue,
        "total_profit": total_profit,
    }
    return target_date, totals, transactions


@app.get("/reports", response_model=ReportResponse)
def get_reports(date_utc: Optional[str] = None, ctx: RequestContext = Depends(get_current_context)):
    """
    Returns totals for the given UTC date (YYYY-MM-DD). Defaults to today (UTC).
    """
    _, totals, transactions = _build_daily_report(ctx, date_utc)
    return {
        "totals": totals,
        "transactions": transactions,
    }

# Billing endpoints
class CheckoutRequest(BaseModel):
    plan: str  # 'pro' or 'business'


@app.get("/billing/config")
def get_billing_config():
    """Get Stripe publishable key and price IDs for frontend."""
    return {
        "publishable_key": get_publishable_key(),
        "prices": {
            "pro": {
                "id": os.getenv("STRIPE_PRO_PRICE_ID", ""),
                "amount": 25000,
                "currency": "ZAR",
                "name": "Pro Plan",
                "description": "Unlimited products, 3 users, CSV export, low-stock alerts"
            },
            "business": {
                "id": os.getenv("STRIPE_BUSINESS_PRICE_ID", ""),
                "amount": 35000,
                "currency": "ZAR",
                "name": "Business Plan",
                "description": "Everything in Pro + unlimited users, audit logs"
            }
        }
    }


@app.post("/billing/checkout")
def create_checkout_session(body: CheckoutRequest, ctx: RequestContext = Depends(get_current_context)):
    stripe = get_stripe_client()
    supa = get_supabase_client()
    
    try:
        sub_res = supa.table("subscriptions").select("*").eq("store_id", ctx.store_id).single().execute()
        sub_data = sub_res.data or {}
    except Exception:
        sub_data = {}
    
    customer_id = sub_data.get("stripe_customer_id")
    if not customer_id:
        customer = stripe.Customer.create(
            metadata={"store_id": ctx.store_id}
        )
        customer_id = customer["id"]
        supa.table("subscriptions").upsert({
            "store_id": ctx.store_id,
            "stripe_customer_id": customer_id,
        }).execute()
    
    price_map = {
        "pro": os.getenv("STRIPE_PRO_PRICE_ID", ""),
        "business": os.getenv("STRIPE_BUSINESS_PRICE_ID", ""),
    }
    price_id = price_map.get(body.plan)
    if not price_id:
        raise HTTPException(status_code=400, detail="Invalid plan or missing price ID. Set STRIPE_PRO_PRICE_ID and STRIPE_BUSINESS_PRICE_ID in .env")
    
    # Use FRONTEND_URL for redirects (set in .env)
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
    success_url = f"{frontend_url}/billing?success=1"
    cancel_url = f"{frontend_url}/billing?canceled=1"
    
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        subscription_data={
            "trial_period_days": 7,  # 7-day free trial
            "metadata": {"store_id": ctx.store_id, "plan": body.plan}
        },
        metadata={"store_id": ctx.store_id, "plan": body.plan},
    )
    return {"url": session["url"]}


@app.post("/billing/portal")
def create_customer_portal(ctx: RequestContext = Depends(get_current_context)):
    """Create a Stripe customer portal session for managing subscription."""
    stripe = get_stripe_client()
    supa = get_supabase_client()
    
    try:
        sub_res = supa.table("subscriptions").select("*").eq("store_id", ctx.store_id).single().execute()
        sub_data = sub_res.data or {}
    except Exception:
        raise HTTPException(status_code=400, detail="No subscription found")
    
    customer_id = sub_data.get("stripe_customer_id")
    if not customer_id:
        raise HTTPException(status_code=400, detail="No Stripe customer found. Please subscribe first.")
    
    # Use FRONTEND_URL for redirects (set in .env)
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
    return_url = f"{frontend_url}/billing"
    
    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=return_url,
    )
    return {"url": session["url"]}


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events for subscription updates."""
    payload = await request.body()
    sig = request.headers.get("stripe-signature")
    stripe = get_stripe_client()
    supa = get_supabase_client()
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")
    
    if not webhook_secret:
        raise HTTPException(status_code=500, detail="Missing STRIPE_WEBHOOK_SECRET")
    if not sig:
        raise HTTPException(status_code=400, detail="Missing Stripe signature header")
    
    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=sig,
            secret=webhook_secret,
        )
    except Exception as e:
        print(f"[Stripe Webhook] Error verifying signature: {e}")
        raise HTTPException(status_code=400, detail="Invalid webhook signature")
    
    event_type = event["type"]
    print(f"[Stripe Webhook] Received event: {event_type}")
    
    if event_type in ("customer.subscription.created", "customer.subscription.updated"):
        sub = event["data"]["object"]
        store_id = sub.get("metadata", {}).get("store_id")
        plan = sub.get("metadata", {}).get("plan", "pro")
        status = sub.get("status", "active")
        
        if store_id:
            period_end = sub.get("current_period_end")
            trial_end = sub.get("trial_end")
            
            update_data = {
                "store_id": store_id,
                "plan": plan,
                "status": status,
                "stripe_customer_id": sub.get("customer"),
                "stripe_subscription_id": sub.get("id"),
            }
            
            if period_end:
                update_data["current_period_end"] = datetime.fromtimestamp(period_end, tz=timezone.utc).isoformat()
            if trial_end:
                update_data["trial_end"] = datetime.fromtimestamp(trial_end, tz=timezone.utc).isoformat()
            
            supa.table("subscriptions").upsert(update_data).execute()
            print(f"[Stripe Webhook] Updated subscription for store {store_id}: {plan} ({status})")
    
    elif event_type == "customer.subscription.deleted":
        sub = event["data"]["object"]
        store_id = sub.get("metadata", {}).get("store_id")
        
        if store_id:
            supa.table("subscriptions").upsert({
                "store_id": store_id,
                "status": "canceled",
                "plan": "expired",
                "stripe_customer_id": sub.get("customer"),
                "stripe_subscription_id": sub.get("id"),
            }).execute()
            print(f"[Stripe Webhook] Subscription canceled for store {store_id}")
    
    elif event_type == "invoice.payment_failed":
        invoice = event["data"]["object"]
        customer_id = invoice.get("customer")
        
        if customer_id:
            subs = supa.table("subscriptions").select("*").eq("stripe_customer_id", customer_id).execute()
            for sub_data in (subs.data or []):
                supa.table("subscriptions").update({
                    "status": "past_due"
                }).eq("store_id", sub_data["store_id"]).execute()
                print(f"[Stripe Webhook] Payment failed for store {sub_data['store_id']}")
    
    elif event_type == "checkout.session.completed":
        session = event["data"]["object"]
        store_id = session.get("metadata", {}).get("store_id")
        plan = session.get("metadata", {}).get("plan")
        subscription_id = session.get("subscription")
        customer_id = session.get("customer")
        
        if store_id and subscription_id:
            try:
                stripe_sub = stripe.Subscription.retrieve(subscription_id)
                status = stripe_sub.get("status", "active")
                trial_end = stripe_sub.get("trial_end")
                period_end = stripe_sub.get("current_period_end")
                
                update_data = {
                    "store_id": store_id,
                    "plan": plan,
                    "status": status,
                    "stripe_customer_id": customer_id,
                    "stripe_subscription_id": subscription_id,
                }
                
                if trial_end:
                    update_data["trial_end"] = datetime.fromtimestamp(trial_end, tz=timezone.utc).isoformat()
                if period_end:
                    update_data["current_period_end"] = datetime.fromtimestamp(period_end, tz=timezone.utc).isoformat()
                
                supa.table("subscriptions").upsert(update_data).execute()
                print(f"[Stripe Webhook] Checkout completed for store {store_id}: {plan} ({status})")
            except Exception as e:
                print(f"[Stripe Webhook] Error fetching subscription {subscription_id}: {e}")
                supa.table("subscriptions").upsert({
                    "store_id": store_id,
                    "plan": plan,
                    "status": "trialing",
                    "stripe_customer_id": customer_id,
                    "stripe_subscription_id": subscription_id,
                }).execute()
    
    return {"received": True}


# Plan Info endpoint
@app.get("/plan")
def get_current_plan(ctx: RequestContext = Depends(get_current_context)):
    return get_plan_info(ctx.store_id)


# Low-stock alerts endpoint (Pro/Business feature)
class LowStockProduct(BaseModel):
    id: int
    sku: Optional[str] = None
    name: str
    quantity: int
    threshold: int = 10


@app.get("/alerts/low-stock", response_model=List[LowStockProduct])
def get_low_stock_alerts(
    threshold: int = 10,
    ctx: RequestContext = Depends(get_current_context)
):
    limits = get_store_plan(ctx.store_id)
    if not limits.allow_low_stock_alerts:
        raise HTTPException(status_code=402, detail="Low-stock alerts require Pro or Business plan")
    
    if threshold < 0 or threshold > 1000:
        threshold = 10
    
    supabase = get_supabase_client()
    try:
        res = (
            supabase.table("products")
            .select("id,sku,name,quantity")
            .eq("store_id", ctx.store_id)
            .lte("quantity", threshold)
            .order("quantity")
            .execute()
        )
        products = res.data or []
        return [{"threshold": threshold, **p} for p in products]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# CSV Export endpoint (Pro/Business feature)
@app.get("/reports/export")
def export_reports_csv(
    date_utc: Optional[str] = None,
    ctx: RequestContext = Depends(get_current_context)
):
    limits = get_store_plan(ctx.store_id)
    if not limits.allow_csv_export:
        raise HTTPException(status_code=402, detail="CSV export requires Pro or Business plan")
    
    supabase = get_supabase_client()
    if date_utc:
        try:
            target_date = datetime.fromisoformat(date_utc).date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    else:
        target_date = datetime.now(timezone.utc).date()

    day_start = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc).isoformat()
    day_end = datetime.combine(target_date, datetime.max.time(), tzinfo=timezone.utc).isoformat()

    try:
        sales_res = (
            supabase.table("sales")
            .select("*")
            .eq("store_id", ctx.store_id)
            .gte("timestamp", day_start)
            .lte("timestamp", day_end)
            .order("timestamp", desc=True)
            .execute()
        )
        transactions = sales_res.data or []
        
        products_res = supabase.table("products").select("id,name,sku").eq("store_id", ctx.store_id).execute()
        product_names = {p["id"]: {"name": p["name"], "sku": p.get("sku", "")} for p in (products_res.data or [])}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Sale ID", "Product ID", "Product SKU", "Product Name", "Quantity Sold", "Total Price", "Timestamp"])
    
    for tx in transactions:
        product_info = product_names.get(tx["product_id"], {"name": "Unknown", "sku": ""})
        writer.writerow([
            tx["id"],
            tx["product_id"],
            product_info["sku"],
            product_info["name"],
            tx["quantity_sold"],
            tx["total_price"],
            tx["timestamp"]
        ])
    
    csv_content = output.getvalue()
    output.close()
    
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=sales_report_{target_date}.csv"}
    )


# Audit log endpoint (Business only)
class AuditLogEntry(BaseModel):
    id: int
    user_id: str
    action: str
    resource_type: str
    resource_id: Optional[str] = None
    details: Optional[str] = None
    timestamp: datetime


@app.get("/audit-logs", response_model=List[AuditLogEntry])
def get_audit_logs(
    limit: int = 50,
    ctx: RequestContext = Depends(get_current_context)
):
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    
    limits = get_store_plan(ctx.store_id)
    if not limits.allow_audit_logs:
        raise HTTPException(status_code=402, detail="Audit logs require Business plan")
    
    if limit < 1 or limit > 200:
        limit = 50
    
    supabase = get_supabase_client()
    try:
        res = (
            supabase.table("audit_logs")
            .select("*")
            .eq("store_id", ctx.store_id)
            .order("timestamp", desc=True)
            .limit(limit)
            .execute()
        )
        return res.data or []
    except Exception as e:
        return []


def log_audit_event(store_id: str, user_id: str, action: str, resource_type: str, resource_id: str = None, details: str = None):
    try:
        supabase = get_supabase_client()
        limits = get_store_plan(store_id)
        if limits.allow_audit_logs:
            supabase.table("audit_logs").insert({
                "store_id": store_id,
                "user_id": user_id,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "details": details,
                "timestamp": _now_utc_iso()
            }).execute()
    except Exception:
        pass


# Analytics endpoint (Pro/Business feature)
from .analytics import get_analytics, AnalyticsSummary

@app.get("/analytics", response_model=AnalyticsSummary)
def get_store_analytics(
    days: int = 30,
    ctx: RequestContext = Depends(get_current_context)
):
    limits = get_store_plan(ctx.store_id)
    if not limits.allow_advanced_reports:
        raise HTTPException(status_code=402, detail="Advanced analytics require Pro or Business plan")
    
    if days < 1:
        days = 1
    elif days > 365:
        days = 365
    
    return get_analytics(ctx.store_id, days)


# Notification infrastructure endpoints (Email only via Brevo)
from .notifications import (
    is_email_configured,
    send_email,
    generate_receipt_html,
    generate_low_stock_email,
    generate_daily_summary_email,
    NotificationResult, ReceiptRequest
)

class NotificationStatus(BaseModel):
    email_configured: bool


@app.get("/notifications/status", response_model=NotificationStatus)
def get_notification_status(ctx: RequestContext = Depends(get_current_context)):
    return NotificationStatus(
        email_configured=is_email_configured()
    )


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


def _fetch_notification_settings(supabase, store_id: str) -> dict:
    try:
        res = (
            supabase.table("notification_settings")
            .select("notification_email,low_stock_threshold,daily_summary_enabled")
            .eq("store_id", store_id)
            .single()
            .execute()
        )
        return res.data or {}
    except Exception:
        return {}


@app.get("/notifications/settings", response_model=NotificationSettings)
def get_notification_settings(ctx: RequestContext = Depends(get_current_context)):
    supabase = get_supabase_client()
    settings = _fetch_notification_settings(supabase, ctx.store_id)
    return settings or NotificationSettings().model_dump()


@app.put("/notifications/settings", response_model=NotificationSettings)
def update_notification_settings(
    settings: NotificationSettings,
    ctx: RequestContext = Depends(get_current_context)
):
    supabase = get_supabase_client()
    payload = settings.model_dump()
    payload["store_id"] = ctx.store_id
    try:
        res = (
            supabase.table("notification_settings")
            .upsert(payload, on_conflict="store_id")
            .execute()
        )
        return res.data[0] if res.data else settings.model_dump()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/notifications/low-stock", response_model=NotificationResponse)
def send_low_stock_notification(
    request: SendLowStockAlertRequest,
    ctx: RequestContext = Depends(get_current_context)
):
    limits = get_store_plan(ctx.store_id)
    if not limits.allow_low_stock_alerts:
        raise HTTPException(status_code=402, detail="Low-stock alerts require Pro or Business plan")
    
    supabase = get_supabase_client()
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
        return NotificationResponse(
            success=True,
            results=[],
            message="No products below threshold"
        )
    
    results = []
    email_to_use = request.email
    if request.send_email and not email_to_use:
        settings = _fetch_notification_settings(supabase, ctx.store_id)
        email_to_use = settings.get("notification_email")
        if not email_to_use:
            return NotificationResponse(
                success=False,
                results=[],
                message="Notification email not configured"
            )

    if request.send_email and email_to_use:
        subject, html_body = generate_low_stock_email(low_stock_products)
        result = send_email(email_to_use, subject, html_body)
        results.append(result.model_dump())
    
    all_success = all(r.get("success", False) for r in results) if results else True
    
    return NotificationResponse(
        success=all_success,
        results=results,
        message=f"Processed notifications for {len(low_stock_products)} low-stock products"
    )


class DailySummaryRequest(BaseModel):
    date_utc: Optional[str] = None
    email: Optional[str] = None
    send_email: bool = False


@app.post("/notifications/daily-summary", response_model=NotificationResponse)
def send_daily_summary_notification(
    request: DailySummaryRequest,
    ctx: RequestContext = Depends(get_current_context)
):
    target_date, totals, _transactions = _build_daily_report(ctx, request.date_utc)
    date_label = target_date.strftime("%Y-%m-%d")
    summary_payload = {
        "date_label": date_label,
        "totals": totals,
    }

    results = []
    supabase = get_supabase_client()
    email_to_use = request.email
    if request.send_email and not email_to_use:
        settings = _fetch_notification_settings(supabase, ctx.store_id)
        if settings.get("daily_summary_enabled"):
            email_to_use = settings.get("notification_email")
        if not email_to_use:
            return NotificationResponse(
                success=False,
                results=[],
                payload=summary_payload,
                message="Notification email not configured"
            )

    if request.send_email and email_to_use:
        store_name = "Kash-Flow"
        try:
            store_res = supabase.table("stores").select("name").eq("id", ctx.store_id).single().execute()
            store_name = store_res.data.get("name") or store_name
        except Exception:
            pass
        subject, html_body = generate_daily_summary_email(
            {"date_label": date_label, "totals": totals},
            store_name=store_name
        )
        result = send_email(email_to_use, subject, html_body)
        results.append(result.model_dump())

    all_success = all(r.get("success", False) for r in results) if results else True

    return NotificationResponse(
        success=all_success,
        results=results,
        payload=summary_payload,
        message="Daily summary processed"
    )


@app.post("/receipts/send", response_model=NotificationResponse)
def send_receipt(
    request: ReceiptRequest,
    ctx: RequestContext = Depends(get_current_context)
):
    supabase = get_supabase_client()
    try:
        sale_res = supabase.table("sales").select("*").eq("id", request.sale_id).eq("store_id", ctx.store_id).single().execute()
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
        "items": [{
            "name": product.get("name", "Product"),
            "quantity": sale.get("quantity_sold", 1),
            "price": float(product.get("price", 0)),
            "total": total_price
        }],
        # Payment info from request
        "payment_method": request.payment_method or "cash",
        "payment_amount": request.payment_amount or total_price,
        "change": request.change_amount or 0
    }
    
    results = []
    
    if request.send_email and request.customer_email:
        html_body = generate_receipt_html(sale_data)
        result = send_email(
            request.customer_email,
            f"Receipt #{sale['id']} - Kash-Flow",
            html_body
        )
        results.append(result.model_dump())
    
    all_success = all(r.get("success", False) for r in results) if results else True
    
    return NotificationResponse(
        success=all_success,
        results=results,
        message="Receipt sent" if all_success else "Failed to send receipt"
    )


# Health
class HealthResponse(BaseModel):
    status: str
    time: str


@app.get("/health", response_model=HealthResponse)
def health():
    return {"status": "ok", "time": _now_utc_iso()}


