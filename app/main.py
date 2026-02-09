import os
from datetime import datetime, timezone, date
from typing import List, Optional
import base64
import hashlib
from cryptography.fernet import Fernet

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
    Category,
    CategoryCreate,
    CategoryUpdate,
    Customer,
    CustomerCreate,
    CustomerUpdate,
    Discount,
    DiscountCreate,
    DiscountUpdate,
    ApplyDiscountRequest,
    ApplyDiscountResponse,
    # Expense schemas
    Expense,
    ExpenseCreate,
    ExpenseUpdate,
    ExpenseCategory,
    # Employee/Shift schemas
    Shift,
    ShiftCreate,
    ShiftUpdate,
    ClockInRequest,
    ClockOutRequest,
    TimeClockEntry,
    Commission,
    # Enhanced report schemas
    ProfitLossReport,
    EmployeeSalesReport,
    TaxReport,
    InventoryValuationReport,
    # Barcode schemas
    BarcodeGenerateRequest,
    BarcodeResponse,
    BarcodeLookupResponse,
    # Privacy schemas
    UserConsent,
    ConsentUpdate,
    PrivacySettings,
    PrivacySettingsUpdate,
    UserSession,
    DataExportRequest,
    AccountDeletionRequest,
    AccountDeletionCreate,
    CookiePreferences,
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


# Returns
class ReturnCreate(BaseModel):
    product_id: int
    quantity_returned: int
    reason: Optional[str] = None
    original_sale_id: Optional[int] = None


class ReturnResponse(BaseModel):
    id: int
    product_id: int
    quantity_returned: int
    refund_amount: float
    reason: Optional[str] = None
    original_sale_id: Optional[int] = None
    returned_by: Optional[str] = None
    timestamp: str
    store_id: str


@app.post("/returns", response_model=ReturnResponse, status_code=201)
def process_return(payload: ReturnCreate, ctx: RequestContext = Depends(get_current_context)):
    """
    Process a product return:
    - Add stock back to inventory
    - Record the return transaction
    - Calculate refund amount
    """
    supabase = get_supabase_client()
    
    try:
        # Get product details for refund calculation
        product_res = supabase.table("products").select("*").eq("id", payload.product_id).eq("store_id", ctx.store_id).single().execute()
        if not product_res.data:
            raise HTTPException(status_code=404, detail="Product not found")
        
        product = product_res.data
        refund_amount = product["price"] * payload.quantity_returned
        
        # Update product quantity (add stock back)
        new_quantity = (product.get("quantity") or 0) + payload.quantity_returned
        supabase.table("products").update({
            "quantity": new_quantity,
            "updated_at": _now_utc_iso()
        }).eq("id", payload.product_id).execute()
        
        # Record the return in sales table with negative quantity
        return_record = supabase.table("sales").insert({
            "store_id": ctx.store_id,
            "product_id": payload.product_id,
            "quantity_sold": -payload.quantity_returned,  # Negative for returns
            "total_price": -refund_amount,  # Negative for refund
            "sold_by": ctx.user_id,
            "timestamp": _now_utc_iso()
        }).execute()
        
        if not return_record.data:
            raise HTTPException(status_code=500, detail="Failed to record return")
        
        record = return_record.data[0]
        
        # Log audit event
        log_audit_event(
            ctx.store_id, 
            ctx.user_id, 
            "create", 
            "return", 
            str(record.get("id", "")), 
            f"Return: product {payload.product_id}, qty {payload.quantity_returned}, refund R{refund_amount:.2f}"
        )
        
        return {
            "id": record["id"],
            "product_id": payload.product_id,
            "quantity_returned": payload.quantity_returned,
            "refund_amount": refund_amount,
            "reason": payload.reason,
            "original_sale_id": payload.original_sale_id,
            "returned_by": ctx.user_id,
            "timestamp": record["timestamp"],
            "store_id": ctx.store_id
        }
        
    except HTTPException:
        raise
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
        cost_by_id = {}
        for p in (products_res.data or []):
            pid = int(p["id"])
            cost_val = p.get("cost_price")
            # Handle None, 0, or numeric values
            if cost_val is None:
                cost_by_id[pid] = 0.0
            else:
                try:
                    cost_by_id[pid] = float(cost_val)
                except (ValueError, TypeError):
                    cost_by_id[pid] = 0.0
    except Exception:
        cost_by_id = {}
    total_profit = 0.0
    for tx in transactions:
        pid = int(tx["product_id"])
        qty = int(tx["quantity_sold"])
        cost = cost_by_id.get(pid, 0.0)
        revenue = float(tx["total_price"])
        profit = revenue - (cost * qty)
        total_profit += profit
        # Add profit to transaction object (ensure it's a float)
        tx["profit"] = round(float(profit), 2)

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
    Admin only - contains sensitive financial data.
    """
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
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
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
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
        payment_method_collection="always",  # Require credit card even for trial
        subscription_data={
            "trial_period_days": 30,  # 30-day free trial (1 month)
            "metadata": {"store_id": ctx.store_id, "plan": body.plan}
        },
        metadata={"store_id": ctx.store_id, "plan": body.plan},
    )
    return {"url": session["url"]}


@app.post("/billing/portal")
def create_customer_portal(ctx: RequestContext = Depends(get_current_context)):
    """Create a Stripe customer portal session for managing subscription."""
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
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
@app.get("/profile")
def get_profile(ctx: RequestContext = Depends(get_current_context)):
    """Get current user's profile including role."""
    supabase = get_supabase_client()
    try:
        # Get profile from database to include name
        prof_result = supabase.table("profiles").select("*").eq("id", ctx.user_id).execute()
        
        if prof_result.data and len(prof_result.data) > 0:
            profile_data = prof_result.data[0]
            return {
                "id": profile_data["id"],
                "name": profile_data.get("name"),
                "role": profile_data.get("role", ctx.role),  # Fallback to context role
                "store_id": profile_data.get("store_id", ctx.store_id),
            }
        else:
            # Profile should exist because get_current_context creates it, but return context data as fallback
            return {
                "id": ctx.user_id,
                "name": None,
                "role": ctx.role,
                "store_id": ctx.store_id,
            }
    except Exception as e:
        # If database query fails, return data from context as fallback
        print(f"[Profile API] Error fetching profile: {e}")
        return {
            "id": ctx.user_id,
            "name": None,
            "role": ctx.role,
            "store_id": ctx.store_id,
        }


# User Management endpoints (Admin only)
class UserResponse(BaseModel):
    id: str
    email: str
    name: Optional[str] = None
    role: str
    created_at: Optional[str] = None
    password: Optional[str] = None  # Password for newly created accounts
    login_username: Optional[str] = None  # Username/email to use for login


class InviteUserRequest(BaseModel):
    role: str = "cashier"  # 'admin' or 'cashier'
    # Username and password are always auto-generated


class UpdateUserRoleRequest(BaseModel):
    role: str  # 'admin' or 'cashier'


@app.get("/users", response_model=List[UserResponse])
def list_users(ctx: RequestContext = Depends(get_current_context)):
    """List all users in the current store. Admin only."""
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    
    supabase = get_supabase_client()
    import httpx
    
    try:
        # Get all profiles for this store
        prof_result = supabase.table("profiles").select("*").eq("store_id", ctx.store_id).execute()
        
        if not prof_result.data:
            return []
        
        # Get user emails from auth.users via REST API
        supabase_url = os.getenv("SUPABASE_URL")
        service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        
        users = []
        for profile in prof_result.data:
            user_id = profile["id"]
            email = "unknown"
            created_at = None
            
            try:
                # Fetch user from auth API
                with httpx.Client() as client:
                    resp = client.get(
                        f"{supabase_url}/auth/v1/admin/users/{user_id}",
                        headers={
                            "apikey": service_key,
                            "Authorization": f"Bearer {service_key}"
                        },
                        timeout=5
                    )
                    if resp.status_code == 200:
                        user_data = resp.json()
                        email = user_data.get("email", "unknown")
                        created_at = user_data.get("created_at")
            except Exception:
                pass  # Use defaults if we can't fetch
            
            users.append({
                "id": user_id,
                "email": email,
                "name": profile.get("name"),
                "role": profile.get("role", "cashier"),
                "created_at": created_at,
            })
        
        return users
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/users/invite", response_model=UserResponse)
def invite_user(payload: InviteUserRequest, ctx: RequestContext = Depends(get_current_context)):
    """Create a new user with auto-generated username and password. Admin only.
    
    Username and password are always auto-generated. Password is returned in response.
    """
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    
    if payload.role not in ("admin", "cashier"):
        raise HTTPException(status_code=400, detail="Role must be 'admin' or 'cashier'")
    
    supabase = get_supabase_client()
    import httpx
    import secrets
    import string
    
    # Enforce user limits based on subscription plan
    limits = get_store_plan(ctx.store_id)
    current_users = supabase.table("profiles").select("id").eq("store_id", ctx.store_id).execute()
    current_user_count = len(current_users.data or [])
    
    if current_user_count >= limits.max_users:
        plan_name = limits.plan.capitalize() if limits.plan != "expired" else "Free"
        raise HTTPException(
            status_code=402, 
            detail=f"User limit reached ({limits.max_users} users for {plan_name} plan). Upgrade your plan to add more team members."
        )
    
    supabase_url = os.getenv("SUPABASE_URL")
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    
    # Auto-generate username based on role and existing users
    try:
        # Get existing users in this store to generate unique username
        existing_profiles = supabase.table("profiles").select("name").eq("store_id", ctx.store_id).execute()
        existing_names = [p.get("name", "").lower() for p in (existing_profiles.data or [])]
        
        # Generate unique username
        base_name = "cashier" if payload.role == "cashier" else "admin"
        username = base_name
        counter = 1
        while username.lower() in existing_names or any(name.startswith(username.lower()) for name in existing_names):
            username = f"{base_name}{counter}"
            counter += 1
    except Exception:
        # Fallback if query fails
        username = f"cashier{secrets.randbelow(10000)}" if payload.role == "cashier" else f"admin{secrets.randbelow(10000)}"
    
    # Use fake email format for Supabase compatibility
    email = f"{username}@store.local"
    name = username
    
    try:
        # Check if user already exists by searching for email
        with httpx.Client() as client:
            # List users and find by email
            resp = client.get(
                f"{supabase_url}/auth/v1/admin/users",
                headers={
                    "apikey": service_key,
                    "Authorization": f"Bearer {service_key}"
                },
                params={"page": 1, "per_page": 1000},
                timeout=10
            )
            
            existing_user = None
            if resp.status_code == 200:
                data = resp.json()
                users = data.get("users", [])
                for user in users:
                    if user.get("email") == email:
                        existing_user = user
                        break
            
            if existing_user:
                user_id = existing_user.get("id")
                
                # Check if they already have a profile in this store
                existing_profile = supabase.table("profiles").select("*").eq("id", user_id).eq("store_id", ctx.store_id).execute()
                if existing_profile.data:
                    raise HTTPException(status_code=400, detail="User is already a member of this store")
                
                # For existing users, generate a NEW password and store it
                # (They might have been deleted and re-added, or password was never stored)
                password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))
                
                # Encrypt and store the password
                try:
                    encrypted_password = _encrypt_password(password)
                except Exception as e:
                    encrypted_password = None
                
                # Create profile with encrypted password
                profile_data = {
                    "id": user_id,
                    "name": name,
                    "role": payload.role,
                    "store_id": ctx.store_id,
                }
                if encrypted_password:
                    profile_data["temp_password_encrypted"] = encrypted_password
                
                # Insert profile first, then update password
                profile_data_insert = {k: v for k, v in profile_data.items() if k != 'temp_password_encrypted'}
                supabase.table("profiles").insert(profile_data_insert).execute()
                
                # Update password separately
                if encrypted_password:
                    import time
                    time.sleep(0.3)
                    try:
                        supabase.table("profiles").update({
                            "temp_password_encrypted": encrypted_password
                        }).eq("id", user_id).execute()
                    except Exception:
                        pass
                
                return {
                    "id": user_id,
                    "email": email,
                    "name": name,
                    "role": payload.role,
                    "created_at": existing_user.get("created_at"),
                    "password": password,  # Return the NEW password
                    "login_username": email,
                }
        
        # Create new user
        password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))
        
        with httpx.Client() as client:
            resp = client.post(
                f"{supabase_url}/auth/v1/admin/users",
                headers={
                    "apikey": service_key,
                    "Authorization": f"Bearer {service_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "email": email,
                    "password": password,
                    "email_confirm": True,  # Auto-confirm for username accounts
                    "user_metadata": {"store_name": "Invited User", "username": username}
                },
                timeout=10
            )
            
            if resp.status_code not in (200, 201):
                raise HTTPException(status_code=500, detail=f"Failed to create user: {resp.text}")
            
            user_data = resp.json()
            user_id = user_data.get("id")
        
        # Encrypt password for storage
        try:
            encrypted_password = _encrypt_password(password)
        except Exception:
            encrypted_password = None
        
        # Create profile with encrypted password
        profile_data = {
            "id": user_id,
            "name": name,
            "role": payload.role,
            "store_id": ctx.store_id,
        }
        if encrypted_password:
            profile_data["temp_password_encrypted"] = encrypted_password
        
        try:
            # Insert profile (without password first to ensure insert succeeds)
            profile_data_insert = {k: v for k, v in profile_data.items() if k != 'temp_password_encrypted'}
            supabase.table("profiles").insert(profile_data_insert).execute()
            
            # ALWAYS update password separately to ensure it's stored
            if encrypted_password:
                import time
                time.sleep(0.3)  # Wait for insert to complete
                try:
                    supabase.table("profiles").update({
                        "temp_password_encrypted": encrypted_password
                    }).eq("id", user_id).execute()
                except Exception:
                    pass
        except Exception as e:
            error_msg = str(e)
            # If column doesn't exist, try without password
            if "temp_password_encrypted" in error_msg or "column" in error_msg.lower() or "does not exist" in error_msg.lower():
                profile_data.pop("temp_password_encrypted", None)
                supabase.table("profiles").insert(profile_data).execute()
            else:
                raise
        
        # Always return password and login username
        return {
            "id": user_id,
            "email": email,
            "name": name,
            "role": payload.role,
            "created_at": user_data.get("created_at"),
            "password": password,  # Always return password
            "login_username": email,  # The email format used for login
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to invite user: {str(e)}")


def _get_encryption_key() -> bytes:
    """Get or generate encryption key for password storage."""
    key_env = os.getenv("PASSWORD_ENCRYPTION_KEY")
    if key_env:
        # Use provided key (should be base64-encoded Fernet key)
        try:
            return base64.urlsafe_b64decode(key_env.encode())
        except Exception:
            # If not base64, use it as seed to generate key
            key_hash = hashlib.sha256(key_env.encode()).digest()
            return base64.urlsafe_b64encode(key_hash[:32])
    else:
        # Generate key from SUPABASE_SERVICE_ROLE_KEY as seed (fallback)
        seed = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "default-secret-key")
        key_hash = hashlib.sha256(seed.encode()).digest()
        return base64.urlsafe_b64encode(key_hash[:32])


def _encrypt_password(password: str) -> str:
    """Encrypt password for storage."""
    try:
        key = _get_encryption_key()
        f = Fernet(key)
        encrypted = f.encrypt(password.encode())
        return base64.urlsafe_b64encode(encrypted).decode()
    except Exception as e:
        # Fallback to simple base64 if encryption fails
        return base64.urlsafe_b64encode(password.encode()).decode()


def _decrypt_password(encrypted_password: str) -> str:
    """Decrypt stored password."""
    try:
        key = _get_encryption_key()
        f = Fernet(key)
        encrypted_bytes = base64.urlsafe_b64decode(encrypted_password.encode())
        decrypted = f.decrypt(encrypted_bytes)
        return decrypted.decode()
    except Exception:
        # Fallback: try simple base64 decode
        try:
            return base64.urlsafe_b64decode(encrypted_password.encode()).decode()
        except Exception:
            raise HTTPException(status_code=500, detail="Failed to decrypt password")


class UserCredentialsResponse(BaseModel):
    id: str
    email: str
    name: Optional[str] = None
    login_username: str
    password: str


@app.get("/users/{user_id}/credentials", response_model=UserCredentialsResponse)
def get_user_credentials(user_id: str, ctx: RequestContext = Depends(get_current_context)):
    """Get user credentials (username and password). Admin only."""
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    
    supabase = get_supabase_client()
    
    try:
        # Get user profile from same store
        prof_result = supabase.table("profiles").select("*").eq("id", user_id).eq("store_id", ctx.store_id).single().execute()
        
        if not prof_result.data:
            raise HTTPException(status_code=404, detail="User not found in this store")
        
        profile = prof_result.data
        
        # Get email from Supabase Auth
        supabase_url = os.getenv("SUPABASE_URL")
        service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        
        import httpx
        with httpx.Client() as client:
            resp = client.get(
                f"{supabase_url}/auth/v1/admin/users/{user_id}",
                headers={
                    "apikey": service_key,
                    "Authorization": f"Bearer {service_key}"
                },
                timeout=10
            )
            
            if resp.status_code != 200:
                raise HTTPException(status_code=404, detail="User not found in auth system")
            
            user_data = resp.json()
            email = user_data.get("email", "")
        
        # Decrypt password
        encrypted_password = profile.get("temp_password_encrypted")
        if not encrypted_password:
            raise HTTPException(
                status_code=404, 
                detail="Password not stored for this user. This user may have been created before password storage was enabled, or they may have changed their password."
            )
        
        password = _decrypt_password(encrypted_password)
        
        return {
            "id": user_id,
            "email": email,
            "name": profile.get("name"),
            "login_username": email,
            "password": password,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get credentials: {str(e)}")


@app.put("/users/{user_id}/role", response_model=UserResponse)
def update_user_role(user_id: str, payload: UpdateUserRoleRequest, ctx: RequestContext = Depends(get_current_context)):
    """Update a user's role. Admin only."""
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    
    if payload.role not in ("admin", "cashier"):
        raise HTTPException(status_code=400, detail="Role must be 'admin' or 'cashier'")
    
    supabase = get_supabase_client()
    
    try:
        # Check if user is in the same store
        prof_result = supabase.table("profiles").select("*").eq("id", user_id).eq("store_id", ctx.store_id).single().execute()
        
        if not prof_result.data:
            raise HTTPException(status_code=404, detail="User not found in this store")
        
        # Prevent removing the last admin
        if payload.role == "cashier" and prof_result.data.get("role") == "admin":
            admin_count = supabase.table("profiles").select("id", count="exact").eq("store_id", ctx.store_id).eq("role", "admin").execute()
            if admin_count.count == 1:
                raise HTTPException(status_code=400, detail="Cannot remove the last admin. Promote another user to admin first.")
        
        # Update role
        supabase.table("profiles").update({"role": payload.role}).eq("id", user_id).eq("store_id", ctx.store_id).execute()
        
        # Get updated profile
        updated = supabase.table("profiles").select("*").eq("id", user_id).single().execute()
        
        # Get user email
        supabase_url = os.getenv("SUPABASE_URL")
        service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        email = "unknown"
        
        try:
            import httpx
            with httpx.Client() as client:
                resp = client.get(
                    f"{supabase_url}/auth/v1/admin/users/{user_id}",
                    headers={
                        "apikey": service_key,
                        "Authorization": f"Bearer {service_key}"
                    },
                    timeout=5
                )
                if resp.status_code == 200:
                    user_data = resp.json()
                    email = user_data.get("email", "unknown")
        except Exception:
            pass
        
        return {
            "id": user_id,
            "email": email,
            "name": updated.data.get("name"),
            "role": payload.role,
            "created_at": None,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/users/{user_id}", status_code=204)
def remove_user(user_id: str, ctx: RequestContext = Depends(get_current_context)):
    """Remove a user from the store. Admin only."""
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    
    if user_id == ctx.user_id:
        raise HTTPException(status_code=400, detail="Cannot remove yourself")
    
    supabase = get_supabase_client()
    
    try:
        # Check if user is in the same store
        prof_result = supabase.table("profiles").select("*").eq("id", user_id).eq("store_id", ctx.store_id).single().execute()
        
        if not prof_result.data:
            raise HTTPException(status_code=404, detail="User not found in this store")
        
        # Prevent removing the last admin
        if prof_result.data.get("role") == "admin":
            admin_count = supabase.table("profiles").select("id", count="exact").eq("store_id", ctx.store_id).eq("role", "admin").execute()
            if admin_count.count == 1:
                raise HTTPException(status_code=400, detail="Cannot remove the last admin")
        
        # Delete profile (cascade will handle store relationship)
        supabase.table("profiles").delete().eq("id", user_id).eq("store_id", ctx.store_id).execute()
        
        return None
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
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


# ===========================================
# CATEGORIES ENDPOINTS
# ===========================================
@app.get("/categories", response_model=List[Category])
def list_categories(
    include_inactive: bool = False,
    ctx: RequestContext = Depends(get_current_context)
):
    """List all categories for the store."""
    supabase = get_supabase_client()
    try:
        query = supabase.table("categories").select("*").eq("store_id", ctx.store_id)
        if not include_inactive:
            query = query.eq("is_active", True)
        res = query.order("sort_order").order("name").execute()
        return res.data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/categories", response_model=Category, status_code=201)
def create_category(payload: CategoryCreate, ctx: RequestContext = Depends(get_current_context)):
    """Create a new category. Admin only."""
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    supabase = get_supabase_client()
    try:
        res = supabase.table("categories").insert({
            "store_id": ctx.store_id,
            "name": payload.name,
            "description": payload.description,
            "color": payload.color or "#6366f1",
            "icon": payload.icon or "tag",
            "sort_order": payload.sort_order or 0,
            "is_active": payload.is_active if payload.is_active is not None else True,
        }).execute()
        
        if res.data and len(res.data) > 0:
            log_audit_event(ctx.store_id, ctx.user_id, "create", "category", str(res.data[0]["id"]), f"Created category: {payload.name}")
            return res.data[0]
        raise HTTPException(status_code=500, detail="Failed to create category")
    except Exception as e:
        if "duplicate key" in str(e).lower() or "unique" in str(e).lower():
            raise HTTPException(status_code=400, detail="Category with this name already exists")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/categories/{category_id}", response_model=Category)
def update_category(category_id: str, payload: CategoryUpdate, ctx: RequestContext = Depends(get_current_context)):
    """Update a category. Admin only."""
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    supabase = get_supabase_client()
    update_data = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if not update_data:
        existing = supabase.table("categories").select("*").eq("id", category_id).eq("store_id", ctx.store_id).single().execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Category not found")
        return existing.data
    
    try:
        res = supabase.table("categories").update(update_data).eq("id", category_id).eq("store_id", ctx.store_id).execute()
        if res.data and len(res.data) > 0:
            log_audit_event(ctx.store_id, ctx.user_id, "update", "category", category_id, f"Updated category: {list(update_data.keys())}")
            return res.data[0]
        raise HTTPException(status_code=404, detail="Category not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/categories/{category_id}", status_code=204)
def delete_category(category_id: str, ctx: RequestContext = Depends(get_current_context)):
    """Delete a category. Admin only."""
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    supabase = get_supabase_client()
    try:
        supabase.table("categories").delete().eq("id", category_id).eq("store_id", ctx.store_id).execute()
        log_audit_event(ctx.store_id, ctx.user_id, "delete", "category", category_id, "Deleted category")
        return None
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ===========================================
# CUSTOMERS ENDPOINTS
# ===========================================
@app.get("/customers", response_model=List[Customer])
def list_customers(
    q: Optional[str] = None,
    include_inactive: bool = False,
    ctx: RequestContext = Depends(get_current_context)
):
    """List all customers for the store."""
    supabase = get_supabase_client()
    try:
        query = supabase.table("customers").select("*").eq("store_id", ctx.store_id)
        if not include_inactive:
            query = query.eq("is_active", True)
        if q:
            query = query.or_(f"name.ilike.%{q}%,email.ilike.%{q}%,phone.ilike.%{q}%")
        res = query.order("name").execute()
        return res.data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/customers/{customer_id}", response_model=Customer)
def get_customer(customer_id: str, ctx: RequestContext = Depends(get_current_context)):
    """Get a single customer by ID."""
    supabase = get_supabase_client()
    try:
        res = supabase.table("customers").select("*").eq("id", customer_id).eq("store_id", ctx.store_id).single().execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Customer not found")
        return res.data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/customers/{customer_id}/purchases", response_model=List[Sale])
def get_customer_purchases(customer_id: str, limit: int = 50, ctx: RequestContext = Depends(get_current_context)):
    """Get purchase history for a customer."""
    supabase = get_supabase_client()
    try:
        res = (
            supabase.table("sales")
            .select("*")
            .eq("store_id", ctx.store_id)
            .eq("customer_id", customer_id)
            .order("timestamp", desc=True)
            .limit(limit)
            .execute()
        )
        return res.data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/customers", response_model=Customer, status_code=201)
def create_customer(payload: CustomerCreate, ctx: RequestContext = Depends(get_current_context)):
    """Create a new customer. Admin only."""
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    supabase = get_supabase_client()
    try:
        insert_data = {
            "store_id": ctx.store_id,
            "name": payload.name,
        }
        if payload.email:
            insert_data["email"] = payload.email
        if payload.phone:
            insert_data["phone"] = payload.phone
        if payload.address:
            insert_data["address"] = payload.address
        if payload.notes:
            insert_data["notes"] = payload.notes
        if payload.birthday:
            insert_data["birthday"] = payload.birthday.isoformat()
            
        res = supabase.table("customers").insert(insert_data).execute()
        
        if res.data and len(res.data) > 0:
            log_audit_event(ctx.store_id, ctx.user_id, "create", "customer", str(res.data[0]["id"]), f"Created customer: {payload.name}")
            return res.data[0]
        raise HTTPException(status_code=500, detail="Failed to create customer")
    except Exception as e:
        if "duplicate key" in str(e).lower() or "unique" in str(e).lower():
            raise HTTPException(status_code=400, detail="Customer with this email or phone already exists")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/customers/{customer_id}", response_model=Customer)
def update_customer(customer_id: str, payload: CustomerUpdate, ctx: RequestContext = Depends(get_current_context)):
    """Update a customer. Admin only."""
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    supabase = get_supabase_client()
    update_data = {}
    for k, v in payload.model_dump(exclude_unset=True).items():
        if v is not None:
            if k == "birthday" and v:
                update_data[k] = v.isoformat()
            else:
                update_data[k] = v
    
    if not update_data:
        existing = supabase.table("customers").select("*").eq("id", customer_id).eq("store_id", ctx.store_id).single().execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Customer not found")
        return existing.data
    
    try:
        res = supabase.table("customers").update(update_data).eq("id", customer_id).eq("store_id", ctx.store_id).execute()
        if res.data and len(res.data) > 0:
            log_audit_event(ctx.store_id, ctx.user_id, "update", "customer", customer_id, f"Updated customer: {list(update_data.keys())}")
            return res.data[0]
        raise HTTPException(status_code=404, detail="Customer not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/customers/{customer_id}", status_code=204)
def delete_customer(customer_id: str, ctx: RequestContext = Depends(get_current_context)):
    """Delete a customer. Admin only."""
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    supabase = get_supabase_client()
    try:
        supabase.table("customers").delete().eq("id", customer_id).eq("store_id", ctx.store_id).execute()
        log_audit_event(ctx.store_id, ctx.user_id, "delete", "customer", customer_id, "Deleted customer")
        return None
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/customers/{customer_id}/add-points", response_model=Customer)
def add_loyalty_points(customer_id: str, points: int, ctx: RequestContext = Depends(get_current_context)):
    """Add loyalty points to a customer. Admin only."""
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    supabase = get_supabase_client()
    try:
        # Get current points
        existing = supabase.table("customers").select("loyalty_points").eq("id", customer_id).eq("store_id", ctx.store_id).single().execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Customer not found")
        
        new_points = (existing.data.get("loyalty_points") or 0) + points
        res = supabase.table("customers").update({"loyalty_points": new_points}).eq("id", customer_id).eq("store_id", ctx.store_id).execute()
        
        if res.data and len(res.data) > 0:
            return res.data[0]
        raise HTTPException(status_code=500, detail="Failed to update points")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ===========================================
# DISCOUNTS ENDPOINTS
# ===========================================
@app.get("/discounts", response_model=List[Discount])
def list_discounts(
    include_inactive: bool = False,
    include_expired: bool = False,
    ctx: RequestContext = Depends(get_current_context)
):
    """List all discounts for the store. Admin only."""
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    supabase = get_supabase_client()
    try:
        query = supabase.table("discounts").select("*").eq("store_id", ctx.store_id)
        if not include_inactive:
            query = query.eq("is_active", True)
        res = query.order("created_at", desc=True).execute()
        
        discounts = res.data or []
        if not include_expired:
            now = datetime.now(timezone.utc)
            discounts = [
                d for d in discounts
                if not d.get("end_date") or datetime.fromisoformat(d["end_date"].replace("Z", "+00:00")) > now
            ]
        return discounts
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/discounts/{discount_id}", response_model=Discount)
def get_discount(discount_id: str, ctx: RequestContext = Depends(get_current_context)):
    """Get a single discount by ID. Admin only."""
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    supabase = get_supabase_client()
    try:
        res = supabase.table("discounts").select("*").eq("id", discount_id).eq("store_id", ctx.store_id).single().execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Discount not found")
        return res.data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/discounts", response_model=Discount, status_code=201)
def create_discount(payload: DiscountCreate, ctx: RequestContext = Depends(get_current_context)):
    """Create a new discount/coupon. Admin only."""
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    
    # Validate percentage discounts
    if payload.discount_type == "percentage" and payload.discount_value > 100:
        raise HTTPException(status_code=400, detail="Percentage discount cannot exceed 100%")
    
    supabase = get_supabase_client()
    try:
        insert_data = {
            "store_id": ctx.store_id,
            "name": payload.name,
            "discount_type": payload.discount_type,
            "discount_value": payload.discount_value,
            "min_purchase_amount": payload.min_purchase_amount or 0,
            "applies_to": payload.applies_to or "all",
            "is_active": payload.is_active if payload.is_active is not None else True,
        }
        if payload.description:
            insert_data["description"] = payload.description
        if payload.code:
            insert_data["code"] = payload.code.upper()
        if payload.max_discount_amount:
            insert_data["max_discount_amount"] = payload.max_discount_amount
        if payload.usage_limit:
            insert_data["usage_limit"] = payload.usage_limit
        if payload.per_customer_limit:
            insert_data["per_customer_limit"] = payload.per_customer_limit
        if payload.applies_to_id:
            insert_data["applies_to_id"] = payload.applies_to_id
        if payload.start_date:
            insert_data["start_date"] = payload.start_date.isoformat()
        if payload.end_date:
            insert_data["end_date"] = payload.end_date.isoformat()
            
        res = supabase.table("discounts").insert(insert_data).execute()
        
        if res.data and len(res.data) > 0:
            log_audit_event(ctx.store_id, ctx.user_id, "create", "discount", str(res.data[0]["id"]), f"Created discount: {payload.name}")
            return res.data[0]
        raise HTTPException(status_code=500, detail="Failed to create discount")
    except Exception as e:
        if "duplicate key" in str(e).lower() or "unique" in str(e).lower():
            raise HTTPException(status_code=400, detail="Discount code already exists")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/discounts/{discount_id}", response_model=Discount)
def update_discount(discount_id: str, payload: DiscountUpdate, ctx: RequestContext = Depends(get_current_context)):
    """Update a discount. Admin only."""
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    
    if payload.discount_type == "percentage" and payload.discount_value and payload.discount_value > 100:
        raise HTTPException(status_code=400, detail="Percentage discount cannot exceed 100%")
    
    supabase = get_supabase_client()
    update_data = {}
    for k, v in payload.model_dump(exclude_unset=True).items():
        if v is not None:
            if k == "code" and v:
                update_data[k] = v.upper()
            elif k in ("start_date", "end_date") and v:
                update_data[k] = v.isoformat()
            else:
                update_data[k] = v
    
    if not update_data:
        existing = supabase.table("discounts").select("*").eq("id", discount_id).eq("store_id", ctx.store_id).single().execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Discount not found")
        return existing.data
    
    try:
        res = supabase.table("discounts").update(update_data).eq("id", discount_id).eq("store_id", ctx.store_id).execute()
        if res.data and len(res.data) > 0:
            log_audit_event(ctx.store_id, ctx.user_id, "update", "discount", discount_id, f"Updated discount: {list(update_data.keys())}")
            return res.data[0]
        raise HTTPException(status_code=404, detail="Discount not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/discounts/{discount_id}", status_code=204)
def delete_discount(discount_id: str, ctx: RequestContext = Depends(get_current_context)):
    """Delete a discount. Admin only."""
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    supabase = get_supabase_client()
    try:
        supabase.table("discounts").delete().eq("id", discount_id).eq("store_id", ctx.store_id).execute()
        log_audit_event(ctx.store_id, ctx.user_id, "delete", "discount", discount_id, "Deleted discount")
        return None
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/discounts/apply", response_model=ApplyDiscountResponse)
def apply_discount(payload: ApplyDiscountRequest, ctx: RequestContext = Depends(get_current_context)):
    """Validate and apply a discount code to a cart total."""
    supabase = get_supabase_client()
    try:
        # Find the discount by code
        res = supabase.table("discounts").select("*").eq("store_id", ctx.store_id).eq("code", payload.code.upper()).eq("is_active", True).single().execute()
        
        if not res.data:
            raise HTTPException(status_code=404, detail="Invalid discount code")
        
        discount = res.data
        now = datetime.now(timezone.utc)
        
        # Check validity dates
        if discount.get("start_date"):
            start = datetime.fromisoformat(discount["start_date"].replace("Z", "+00:00"))
            if now < start:
                raise HTTPException(status_code=400, detail="Discount is not yet active")
        
        if discount.get("end_date"):
            end = datetime.fromisoformat(discount["end_date"].replace("Z", "+00:00"))
            if now > end:
                raise HTTPException(status_code=400, detail="Discount has expired")
        
        # Check usage limit
        if discount.get("usage_limit") and discount.get("usage_count", 0) >= discount["usage_limit"]:
            raise HTTPException(status_code=400, detail="Discount usage limit reached")
        
        # Check minimum purchase amount
        if payload.cart_total < discount.get("min_purchase_amount", 0):
            raise HTTPException(
                status_code=400, 
                detail=f"Minimum purchase of R{discount['min_purchase_amount']:.2f} required"
            )
        
        # Check per-customer limit if customer provided
        if payload.customer_id and discount.get("per_customer_limit"):
            usage_res = supabase.table("customer_discount_usage").select("id").eq("customer_id", payload.customer_id).eq("discount_id", discount["id"]).execute()
            if len(usage_res.data or []) >= discount["per_customer_limit"]:
                raise HTTPException(status_code=400, detail="You have already used this discount")
        
        # Calculate discount amount
        if discount["discount_type"] == "percentage":
            discount_amount = payload.cart_total * (discount["discount_value"] / 100)
            if discount.get("max_discount_amount"):
                discount_amount = min(discount_amount, discount["max_discount_amount"])
        else:  # fixed
            discount_amount = min(discount["discount_value"], payload.cart_total)
        
        final_total = payload.cart_total - discount_amount
        
        return ApplyDiscountResponse(
            discount_id=discount["id"],
            discount_name=discount["name"],
            discount_type=discount["discount_type"],
            discount_value=discount["discount_value"],
            discount_amount=round(discount_amount, 2),
            final_total=round(final_total, 2),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
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


# ===========================================
# BARCODE ENDPOINTS
# ===========================================

@app.post("/products/{product_id}/barcode", response_model=BarcodeResponse)
def generate_barcode(
    product_id: int,
    request: BarcodeGenerateRequest = None,
    ctx: RequestContext = Depends(get_current_context)
):
    """Generate and save a barcode for a product."""
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    
    supabase = get_supabase_client()
    
    # Get product
    product_res = supabase.table("products").select("*").eq("id", product_id).eq("store_id", ctx.store_id).single().execute()
    if not product_res.data:
        raise HTTPException(status_code=404, detail="Product not found")
    
    product = product_res.data
    barcode_type = request.barcode_type if request else "CODE128"
    
    # Generate barcode value based on store and product ID
    barcode_value = f"{ctx.store_id[:8]}-{product_id:06d}"
    
    # Generate barcode image
    try:
        import barcode
        from barcode.writer import ImageWriter
        import qrcode
        from io import BytesIO
        
        buffer = BytesIO()
        
        if barcode_type == "QR":
            qr = qrcode.QRCode(version=1, box_size=10, border=4)
            qr.add_data(barcode_value)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            img.save(buffer, format='PNG')
        else:
            # Use python-barcode for CODE128, EAN13, etc.
            barcode_class = barcode.get_barcode_class(barcode_type.lower())
            bc = barcode_class(barcode_value, writer=ImageWriter())
            bc.write(buffer)
        
        buffer.seek(0)
        barcode_image_b64 = base64.b64encode(buffer.read()).decode('utf-8')
        
        # Save barcode to product
        supabase.table("products").update({
            "barcode": barcode_value,
            "barcode_type": barcode_type
        }).eq("id", product_id).eq("store_id", ctx.store_id).execute()
        
        return BarcodeResponse(
            product_id=product_id,
            barcode=barcode_value,
            barcode_type=barcode_type,
            barcode_image=f"data:image/png;base64,{barcode_image_b64}"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate barcode: {str(e)}")


@app.get("/barcode/lookup/{barcode}", response_model=BarcodeLookupResponse)
def lookup_barcode(
    barcode: str,
    ctx: RequestContext = Depends(get_current_context)
):
    """Look up a product by its barcode."""
    supabase = get_supabase_client()
    
    product_res = supabase.table("products").select("*").eq("barcode", barcode).eq("store_id", ctx.store_id).single().execute()
    if not product_res.data:
        raise HTTPException(status_code=404, detail="Product not found for this barcode")
    
    p = product_res.data
    return BarcodeLookupResponse(
        product_id=p["id"],
        name=p["name"],
        price=float(p["price"]),
        quantity=int(p["quantity"]),
        barcode=barcode
    )


# ===========================================
# EXPENSE ENDPOINTS
# ===========================================

@app.get("/expenses", response_model=List[Expense])
def list_expenses(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    category: Optional[str] = None,
    ctx: RequestContext = Depends(get_current_context)
):
    """List expenses with optional filters."""
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    
    supabase = get_supabase_client()
    query = supabase.table("expenses").select("*").eq("store_id", ctx.store_id)
    
    if start_date:
        query = query.gte("expense_date", start_date.isoformat())
    if end_date:
        query = query.lte("expense_date", end_date.isoformat())
    if category:
        query = query.eq("category", category)
    
    res = query.order("expense_date", desc=True).execute()
    return res.data or []


@app.post("/expenses", response_model=Expense, status_code=201)
def create_expense(
    payload: ExpenseCreate,
    ctx: RequestContext = Depends(get_current_context)
):
    """Create a new expense."""
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    
    supabase = get_supabase_client()
    
    expense_data = payload.model_dump()
    expense_data["store_id"] = ctx.store_id
    expense_data["user_id"] = ctx.user_id
    expense_data["expense_date"] = expense_data["expense_date"].isoformat()
    
    try:
        res = supabase.table("expenses").insert(expense_data).execute()
        log_audit_event(ctx.store_id, ctx.user_id, "create", "expense", res.data[0]["id"], f"Created expense: {payload.category} - {payload.amount}")
        return res.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/expenses/{expense_id}", response_model=Expense)
def get_expense(
    expense_id: str,
    ctx: RequestContext = Depends(get_current_context)
):
    """Get a single expense."""
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    
    supabase = get_supabase_client()
    res = supabase.table("expenses").select("*").eq("id", expense_id).eq("store_id", ctx.store_id).single().execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Expense not found")
    return res.data


@app.put("/expenses/{expense_id}", response_model=Expense)
def update_expense(
    expense_id: str,
    payload: ExpenseUpdate,
    ctx: RequestContext = Depends(get_current_context)
):
    """Update an expense."""
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    
    supabase = get_supabase_client()
    update_data = payload.model_dump(exclude_unset=True)
    
    if "expense_date" in update_data and update_data["expense_date"]:
        update_data["expense_date"] = update_data["expense_date"].isoformat()
    
    try:
        res = supabase.table("expenses").update(update_data).eq("id", expense_id).eq("store_id", ctx.store_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Expense not found")
        log_audit_event(ctx.store_id, ctx.user_id, "update", "expense", expense_id, f"Updated expense fields: {list(update_data.keys())}")
        return res.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/expenses/{expense_id}", status_code=204)
def delete_expense(
    expense_id: str,
    ctx: RequestContext = Depends(get_current_context)
):
    """Delete an expense."""
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    
    supabase = get_supabase_client()
    try:
        supabase.table("expenses").delete().eq("id", expense_id).eq("store_id", ctx.store_id).execute()
        log_audit_event(ctx.store_id, ctx.user_id, "delete", "expense", expense_id, "Deleted expense")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return None


@app.get("/expense-categories", response_model=List[ExpenseCategory])
def list_expense_categories(ctx: RequestContext = Depends(get_current_context)):
    """List expense categories for the store."""
    supabase = get_supabase_client()
    res = supabase.table("expense_categories").select("*").eq("store_id", ctx.store_id).order("name").execute()
    
    # If no categories, return defaults
    if not res.data:
        default_categories = [
            {"name": "Rent", "icon": "home", "color": "#ef4444"},
            {"name": "Utilities", "icon": "zap", "color": "#f59e0b"},
            {"name": "Inventory", "icon": "package", "color": "#10b981"},
            {"name": "Payroll", "icon": "users", "color": "#3b82f6"},
            {"name": "Marketing", "icon": "megaphone", "color": "#8b5cf6"},
            {"name": "Equipment", "icon": "wrench", "color": "#6b7280"},
            {"name": "Insurance", "icon": "shield", "color": "#06b6d4"},
            {"name": "Other", "icon": "more-horizontal", "color": "#9ca3af"},
        ]
        # Insert default categories
        for cat in default_categories:
            cat["store_id"] = ctx.store_id
            cat["is_system"] = True
        supabase.table("expense_categories").insert(default_categories).execute()
        res = supabase.table("expense_categories").select("*").eq("store_id", ctx.store_id).order("name").execute()
    
    return res.data or []


@app.get("/expenses/summary")
def get_expense_summary(
    start_date: date,
    end_date: date,
    ctx: RequestContext = Depends(get_current_context)
):
    """Get expense summary for a period."""
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    
    supabase = get_supabase_client()
    
    expenses_res = supabase.table("expenses").select("*").eq("store_id", ctx.store_id).gte("expense_date", start_date.isoformat()).lte("expense_date", end_date.isoformat()).execute()
    expenses = expenses_res.data or []
    
    total_amount = sum(float(e.get("amount", 0)) for e in expenses)
    
    # Group by category
    by_category = {}
    for e in expenses:
        cat = e.get("category", "Other")
        by_category[cat] = by_category.get(cat, 0) + float(e.get("amount", 0))
    
    category_breakdown = [{"category": k, "amount": v} for k, v in sorted(by_category.items(), key=lambda x: -x[1])]
    
    return {
        "period_start": start_date.isoformat(),
        "period_end": end_date.isoformat(),
        "total_expenses": total_amount,
        "expense_count": len(expenses),
        "category_breakdown": category_breakdown
    }


# ===========================================
# EMPLOYEE SHIFT ENDPOINTS
# ===========================================

@app.get("/shifts", response_model=List[Shift])
def list_shifts(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    user_id: Optional[str] = None,
    status: Optional[str] = None,
    ctx: RequestContext = Depends(get_current_context)
):
    """List employee shifts."""
    supabase = get_supabase_client()
    query = supabase.table("employee_shifts").select("*, profiles(name)").eq("store_id", ctx.store_id)
    
    if start_date:
        query = query.gte("shift_date", start_date.isoformat())
    if end_date:
        query = query.lte("shift_date", end_date.isoformat())
    if user_id:
        query = query.eq("user_id", user_id)
    if status:
        query = query.eq("status", status)
    
    # Non-admins can only see their own shifts
    if ctx.role != "admin":
        query = query.eq("user_id", ctx.user_id)
    
    res = query.order("shift_date", desc=True).execute()
    shifts = []
    for s in res.data or []:
        profile = s.pop("profiles", {}) or {}
        s["user_name"] = profile.get("name", "Unknown")
        shifts.append(s)
    return shifts


@app.post("/shifts", response_model=Shift, status_code=201)
def create_shift(
    payload: ShiftCreate,
    ctx: RequestContext = Depends(get_current_context)
):
    """Create a new shift (admin only)."""
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    
    supabase = get_supabase_client()
    
    shift_data = payload.model_dump()
    shift_data["store_id"] = ctx.store_id
    shift_data["shift_date"] = shift_data["shift_date"].isoformat()
    
    try:
        res = supabase.table("employee_shifts").insert(shift_data).execute()
        log_audit_event(ctx.store_id, ctx.user_id, "create", "shift", res.data[0]["id"], f"Created shift for user {payload.user_id}")
        return res.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/shifts/{shift_id}", response_model=Shift)
def update_shift(
    shift_id: str,
    payload: ShiftUpdate,
    ctx: RequestContext = Depends(get_current_context)
):
    """Update a shift."""
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    
    supabase = get_supabase_client()
    update_data = payload.model_dump(exclude_unset=True)
    
    try:
        res = supabase.table("employee_shifts").update(update_data).eq("id", shift_id).eq("store_id", ctx.store_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Shift not found")
        log_audit_event(ctx.store_id, ctx.user_id, "update", "shift", shift_id, f"Updated shift fields: {list(update_data.keys())}")
        return res.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/shifts/{shift_id}", status_code=204)
def delete_shift(
    shift_id: str,
    ctx: RequestContext = Depends(get_current_context)
):
    """Delete a shift."""
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    
    supabase = get_supabase_client()
    try:
        supabase.table("employee_shifts").delete().eq("id", shift_id).eq("store_id", ctx.store_id).execute()
        log_audit_event(ctx.store_id, ctx.user_id, "delete", "shift", shift_id, "Deleted shift")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return None


# ===========================================
# TIME CLOCK ENDPOINTS
# ===========================================

@app.get("/time-clock", response_model=List[TimeClockEntry])
def list_time_entries(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    user_id: Optional[str] = None,
    ctx: RequestContext = Depends(get_current_context)
):
    """List time clock entries."""
    supabase = get_supabase_client()
    query = supabase.table("time_clock").select("*, profiles(name)").eq("store_id", ctx.store_id)
    
    if start_date:
        query = query.gte("clock_in", f"{start_date.isoformat()}T00:00:00Z")
    if end_date:
        query = query.lte("clock_in", f"{end_date.isoformat()}T23:59:59Z")
    if user_id:
        query = query.eq("user_id", user_id)
    
    # Non-admins can only see their own entries
    if ctx.role != "admin":
        query = query.eq("user_id", ctx.user_id)
    
    res = query.order("clock_in", desc=True).execute()
    entries = []
    for e in res.data or []:
        profile = e.pop("profiles", {}) or {}
        e["user_name"] = profile.get("name", "Unknown")
        entries.append(e)
    return entries


@app.post("/time-clock/clock-in", response_model=TimeClockEntry, status_code=201)
def clock_in(
    request: ClockInRequest = None,
    ctx: RequestContext = Depends(get_current_context)
):
    """Clock in for current user."""
    supabase = get_supabase_client()
    
    # Check if already clocked in
    existing = supabase.table("time_clock").select("*").eq("store_id", ctx.store_id).eq("user_id", ctx.user_id).is_("clock_out", "null").execute()
    if existing.data:
        raise HTTPException(status_code=400, detail="Already clocked in. Please clock out first.")
    
    entry_data = {
        "store_id": ctx.store_id,
        "user_id": ctx.user_id,
        "clock_in": _now_utc_iso(),
        "shift_id": request.shift_id if request else None,
        "notes": request.notes if request else None
    }
    
    try:
        res = supabase.table("time_clock").insert(entry_data).execute()
        log_audit_event(ctx.store_id, ctx.user_id, "clock_in", "time_clock", res.data[0]["id"], "User clocked in")
        return res.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/time-clock/clock-out", response_model=TimeClockEntry)
def clock_out(
    request: ClockOutRequest = None,
    ctx: RequestContext = Depends(get_current_context)
):
    """Clock out for current user."""
    supabase = get_supabase_client()
    
    # Find active clock-in
    existing = supabase.table("time_clock").select("*").eq("store_id", ctx.store_id).eq("user_id", ctx.user_id).is_("clock_out", "null").single().execute()
    if not existing.data:
        raise HTTPException(status_code=400, detail="Not clocked in.")
    
    entry = existing.data
    clock_out_time = datetime.now(timezone.utc)
    clock_in_time = datetime.fromisoformat(entry["clock_in"].replace("Z", "+00:00"))
    
    # Calculate hours worked
    total_seconds = (clock_out_time - clock_in_time).total_seconds()
    break_minutes = entry.get("break_minutes", 0) or 0
    total_hours = (total_seconds / 3600) - (break_minutes / 60)
    
    # Calculate overtime (anything over 8 hours)
    overtime_hours = max(0, total_hours - 8)
    
    update_data = {
        "clock_out": _now_utc_iso(),
        "total_hours": round(total_hours, 2),
        "overtime_hours": round(overtime_hours, 2),
        "notes": (entry.get("notes", "") or "") + ("\n" + request.notes if request and request.notes else "")
    }
    
    try:
        res = supabase.table("time_clock").update(update_data).eq("id", entry["id"]).execute()
        log_audit_event(ctx.store_id, ctx.user_id, "clock_out", "time_clock", entry["id"], f"User clocked out. Hours: {total_hours:.2f}")
        return res.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/time-clock/current")
def get_current_clock_status(ctx: RequestContext = Depends(get_current_context)):
    """Get current user's clock status."""
    supabase = get_supabase_client()
    
    existing = supabase.table("time_clock").select("*").eq("store_id", ctx.store_id).eq("user_id", ctx.user_id).is_("clock_out", "null").execute()
    
    if existing.data:
        return {"clocked_in": True, "entry": existing.data[0]}
    return {"clocked_in": False, "entry": None}


# ===========================================
# COMMISSION ENDPOINTS
# ===========================================

@app.get("/commissions", response_model=List[Commission])
def list_commissions(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    user_id: Optional[str] = None,
    status: Optional[str] = None,
    ctx: RequestContext = Depends(get_current_context)
):
    """List commissions."""
    supabase = get_supabase_client()
    query = supabase.table("employee_commissions").select("*, profiles(name)").eq("store_id", ctx.store_id)
    
    if start_date:
        query = query.gte("created_at", f"{start_date.isoformat()}T00:00:00Z")
    if end_date:
        query = query.lte("created_at", f"{end_date.isoformat()}T23:59:59Z")
    if user_id:
        query = query.eq("user_id", user_id)
    if status:
        query = query.eq("status", status)
    
    # Non-admins can only see their own commissions
    if ctx.role != "admin":
        query = query.eq("user_id", ctx.user_id)
    
    res = query.order("created_at", desc=True).execute()
    commissions = []
    for c in res.data or []:
        profile = c.pop("profiles", {}) or {}
        c["user_name"] = profile.get("name", "Unknown")
        commissions.append(c)
    return commissions


@app.post("/commissions/{commission_id}/approve", response_model=Commission)
def approve_commission(
    commission_id: str,
    ctx: RequestContext = Depends(get_current_context)
):
    """Approve a commission."""
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    
    supabase = get_supabase_client()
    
    try:
        res = supabase.table("employee_commissions").update({
            "status": "approved"
        }).eq("id", commission_id).eq("store_id", ctx.store_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Commission not found")
        log_audit_event(ctx.store_id, ctx.user_id, "approve", "commission", commission_id, "Approved commission")
        return res.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/commissions/{commission_id}/pay", response_model=Commission)
def mark_commission_paid(
    commission_id: str,
    ctx: RequestContext = Depends(get_current_context)
):
    """Mark a commission as paid."""
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    
    supabase = get_supabase_client()
    
    try:
        res = supabase.table("employee_commissions").update({
            "status": "paid",
            "paid_at": _now_utc_iso()
        }).eq("id", commission_id).eq("store_id", ctx.store_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Commission not found")
        log_audit_event(ctx.store_id, ctx.user_id, "pay", "commission", commission_id, "Marked commission as paid")
        return res.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ===========================================
# ENHANCED REPORTING ENDPOINTS
# ===========================================

@app.get("/reports/profit-loss", response_model=ProfitLossReport)
def get_profit_loss_report(
    start_date: date,
    end_date: date,
    ctx: RequestContext = Depends(get_current_context)
):
    """Generate a profit & loss report."""
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    
    supabase = get_supabase_client()
    
    # Get sales in period
    sales_res = supabase.table("sales").select("*").eq("store_id", ctx.store_id).gte("timestamp", f"{start_date.isoformat()}T00:00:00Z").lte("timestamp", f"{end_date.isoformat()}T23:59:59Z").execute()
    sales = sales_res.data or []
    
    # Get expenses in period
    expenses_res = supabase.table("expenses").select("*").eq("store_id", ctx.store_id).gte("expense_date", start_date.isoformat()).lte("expense_date", end_date.isoformat()).execute()
    expenses = expenses_res.data or []
    
    # Calculate totals
    total_revenue = sum(float(s.get("total_price", 0)) for s in sales)
    total_profit_from_sales = sum(float(s.get("profit", 0) or 0) for s in sales)
    total_cost_of_goods = total_revenue - total_profit_from_sales
    
    total_expenses = sum(float(e.get("amount", 0)) for e in expenses)
    net_profit = total_profit_from_sales - total_expenses
    
    # Expense breakdown by category
    expense_by_cat = {}
    for e in expenses:
        cat = e.get("category", "Other")
        expense_by_cat[cat] = expense_by_cat.get(cat, 0) + float(e.get("amount", 0))
    expense_breakdown = [{"category": k, "amount": v} for k, v in sorted(expense_by_cat.items(), key=lambda x: -x[1])]
    
    # Revenue by day
    revenue_by_day = {}
    for s in sales:
        day = s.get("timestamp", "")[:10]
        if day not in revenue_by_day:
            revenue_by_day[day] = {"date": day, "revenue": 0, "profit": 0}
        revenue_by_day[day]["revenue"] += float(s.get("total_price", 0))
        revenue_by_day[day]["profit"] += float(s.get("profit", 0) or 0)
    
    return ProfitLossReport(
        period_start=start_date,
        period_end=end_date,
        total_revenue=total_revenue,
        total_cost_of_goods=total_cost_of_goods,
        gross_profit=total_profit_from_sales,
        total_expenses=total_expenses,
        net_profit=net_profit,
        expense_breakdown=expense_breakdown,
        revenue_by_day=sorted(revenue_by_day.values(), key=lambda x: x["date"])
    )


@app.get("/reports/employee-sales", response_model=List[EmployeeSalesReport])
def get_employee_sales_report(
    start_date: date,
    end_date: date,
    ctx: RequestContext = Depends(get_current_context)
):
    """Generate an employee sales performance report."""
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    
    supabase = get_supabase_client()
    
    # Get sales with sold_by
    sales_res = supabase.table("sales").select("*").eq("store_id", ctx.store_id).gte("timestamp", f"{start_date.isoformat()}T00:00:00Z").lte("timestamp", f"{end_date.isoformat()}T23:59:59Z").execute()
    sales = sales_res.data or []
    
    # Get time clock entries for hours worked
    time_res = supabase.table("time_clock").select("*").eq("store_id", ctx.store_id).gte("clock_in", f"{start_date.isoformat()}T00:00:00Z").lte("clock_in", f"{end_date.isoformat()}T23:59:59Z").execute()
    time_entries = time_res.data or []
    
    # Get commissions
    comm_res = supabase.table("employee_commissions").select("*").eq("store_id", ctx.store_id).gte("created_at", f"{start_date.isoformat()}T00:00:00Z").lte("created_at", f"{end_date.isoformat()}T23:59:59Z").execute()
    commissions = comm_res.data or []
    
    # Get all users
    users_res = supabase.table("profiles").select("id, name").eq("store_id", ctx.store_id).execute()
    users = {u["id"]: u["name"] for u in users_res.data or []}
    
    # Aggregate by user
    by_user = {}
    for s in sales:
        user_id = s.get("sold_by") or "unknown"
        if user_id not in by_user:
            by_user[user_id] = {
                "user_id": user_id,
                "user_name": users.get(user_id, "Unknown"),
                "total_sales": 0,
                "total_revenue": 0,
                "total_profit": 0,
                "commission_earned": 0,
                "hours_worked": 0
            }
        by_user[user_id]["total_sales"] += 1
        by_user[user_id]["total_revenue"] += float(s.get("total_price", 0))
        by_user[user_id]["total_profit"] += float(s.get("profit", 0) or 0)
    
    # Add hours worked
    for t in time_entries:
        user_id = t.get("user_id")
        if user_id in by_user:
            by_user[user_id]["hours_worked"] += float(t.get("total_hours", 0) or 0)
    
    # Add commissions
    for c in commissions:
        user_id = c.get("user_id")
        if user_id in by_user:
            by_user[user_id]["commission_earned"] += float(c.get("commission_amount", 0))
    
    # Calculate avg transaction value
    result = []
    for user_id, data in by_user.items():
        if data["total_sales"] > 0:
            data["avg_transaction_value"] = data["total_revenue"] / data["total_sales"]
        else:
            data["avg_transaction_value"] = 0
        result.append(EmployeeSalesReport(**data))
    
    return sorted(result, key=lambda x: -x.total_revenue)


@app.get("/reports/tax", response_model=TaxReport)
def get_tax_report(
    start_date: date,
    end_date: date,
    tax_rate: float = 15.0,  # Default VAT rate
    ctx: RequestContext = Depends(get_current_context)
):
    """Generate a tax report (VAT/GST)."""
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    
    supabase = get_supabase_client()
    
    # Get sales in period
    sales_res = supabase.table("sales").select("*").eq("store_id", ctx.store_id).gte("timestamp", f"{start_date.isoformat()}T00:00:00Z").lte("timestamp", f"{end_date.isoformat()}T23:59:59Z").execute()
    sales = sales_res.data or []
    
    total_sales = sum(float(s.get("total_price", 0)) for s in sales)
    
    # Calculate tax (assuming VAT-inclusive pricing)
    tax_multiplier = tax_rate / 100
    taxable_sales = total_sales / (1 + tax_multiplier)
    tax_collected = total_sales - taxable_sales
    
    return TaxReport(
        period_start=start_date,
        period_end=end_date,
        total_sales=total_sales,
        tax_collected=round(tax_collected, 2),
        tax_rate=tax_rate,
        taxable_sales=round(taxable_sales, 2),
        transactions_count=len(sales)
    )


@app.get("/reports/inventory-valuation", response_model=InventoryValuationReport)
def get_inventory_valuation_report(
    low_stock_threshold: int = 10,
    ctx: RequestContext = Depends(get_current_context)
):
    """Generate an inventory valuation report."""
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    
    supabase = get_supabase_client()
    
    # Get all products with categories
    products_res = supabase.table("products").select("*, categories(name)").eq("store_id", ctx.store_id).execute()
    products = products_res.data or []
    
    total_products = len(products)
    total_quantity = 0
    total_cost_value = 0
    total_retail_value = 0
    low_stock_count = 0
    out_of_stock_count = 0
    
    by_category = {}
    
    for p in products:
        qty = int(p.get("quantity", 0))
        cost = float(p.get("cost_price", 0) or 0)
        price = float(p.get("price", 0))
        
        total_quantity += qty
        total_cost_value += cost * qty
        total_retail_value += price * qty
        
        if qty == 0:
            out_of_stock_count += 1
        elif qty <= low_stock_threshold:
            low_stock_count += 1
        
        # Category aggregation
        cat_data = p.get("categories") or {}
        cat_name = cat_data.get("name", "Uncategorized") if isinstance(cat_data, dict) else "Uncategorized"
        
        if cat_name not in by_category:
            by_category[cat_name] = {"category": cat_name, "quantity": 0, "cost_value": 0, "retail_value": 0}
        by_category[cat_name]["quantity"] += qty
        by_category[cat_name]["cost_value"] += cost * qty
        by_category[cat_name]["retail_value"] += price * qty
    
    return InventoryValuationReport(
        total_products=total_products,
        total_quantity=total_quantity,
        total_cost_value=round(total_cost_value, 2),
        total_retail_value=round(total_retail_value, 2),
        potential_profit=round(total_retail_value - total_cost_value, 2),
        low_stock_count=low_stock_count,
        out_of_stock_count=out_of_stock_count,
        categories=sorted(by_category.values(), key=lambda x: -x["retail_value"])
    )


@app.get("/reports/export/profit-loss")
def export_profit_loss_csv(
    start_date: date,
    end_date: date,
    ctx: RequestContext = Depends(get_current_context)
):
    """Export profit & loss report as CSV."""
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    
    report = get_profit_loss_report(start_date, end_date, ctx)
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Header
    writer.writerow(["Profit & Loss Report"])
    writer.writerow([f"Period: {start_date} to {end_date}"])
    writer.writerow([])
    
    # Summary
    writer.writerow(["Summary"])
    writer.writerow(["Total Revenue", f"R{report.total_revenue:,.2f}"])
    writer.writerow(["Cost of Goods Sold", f"R{report.total_cost_of_goods:,.2f}"])
    writer.writerow(["Gross Profit", f"R{report.gross_profit:,.2f}"])
    writer.writerow(["Total Expenses", f"R{report.total_expenses:,.2f}"])
    writer.writerow(["Net Profit", f"R{report.net_profit:,.2f}"])
    writer.writerow([])
    
    # Expense breakdown
    writer.writerow(["Expense Breakdown"])
    writer.writerow(["Category", "Amount"])
    for e in report.expense_breakdown:
        writer.writerow([e["category"], f"R{e['amount']:,.2f}"])
    writer.writerow([])
    
    # Daily revenue
    writer.writerow(["Daily Revenue & Profit"])
    writer.writerow(["Date", "Revenue", "Profit"])
    for d in report.revenue_by_day:
        writer.writerow([d["date"], f"R{d['revenue']:,.2f}", f"R{d['profit']:,.2f}"])
    
    content = output.getvalue()
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=profit_loss_{start_date}_{end_date}.csv"}
    )


# ===========================================
# PRIVACY & COMPLIANCE ENDPOINTS
# ===========================================

@app.get("/privacy/consents", response_model=List[UserConsent])
def get_user_consents(
    request: Request,
    ctx: RequestContext = Depends(get_current_context)
):
    """Get all consent records for the current user."""
    supabase = get_supabase_client()
    res = supabase.table("user_consents").select("*").eq("user_id", ctx.user_id).execute()
    return res.data or []


@app.post("/privacy/consents", response_model=UserConsent)
def update_consent(
    consent: ConsentUpdate,
    request: Request,
    ctx: RequestContext = Depends(get_current_context)
):
    """Update a consent record."""
    supabase = get_supabase_client()
    
    # Get client info
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent", "")
    
    consent_data = {
        "user_id": ctx.user_id,
        "consent_type": consent.consent_type,
        "consented": consent.consented,
        "consent_version": consent.consent_version,
        "ip_address": ip_address,
        "user_agent": user_agent,
        "consented_at": _now_utc_iso() if consent.consented else None,
        "revoked_at": _now_utc_iso() if not consent.consented else None,
    }
    
    try:
        # Upsert consent
        res = supabase.table("user_consents").upsert(
            consent_data,
            on_conflict="user_id,consent_type"
        ).execute()
        log_audit_event(ctx.store_id, ctx.user_id, "consent_update", "user_consent", 
                       consent.consent_type, f"{'Granted' if consent.consented else 'Revoked'} consent")
        return res.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/privacy/settings", response_model=PrivacySettings)
def get_privacy_settings(ctx: RequestContext = Depends(get_current_context)):
    """Get privacy settings for the current user."""
    supabase = get_supabase_client()
    res = supabase.table("profiles").select(
        "marketing_emails_enabled, push_notifications_enabled, data_analytics_enabled, two_factor_enabled"
    ).eq("id", ctx.user_id).single().execute()
    
    if not res.data:
        return PrivacySettings()
    
    return PrivacySettings(
        marketing_emails_enabled=res.data.get("marketing_emails_enabled", False),
        push_notifications_enabled=res.data.get("push_notifications_enabled", False),
        data_analytics_enabled=res.data.get("data_analytics_enabled", True),
        two_factor_enabled=res.data.get("two_factor_enabled", False)
    )


@app.put("/privacy/settings", response_model=PrivacySettings)
def update_privacy_settings(
    settings: PrivacySettingsUpdate,
    ctx: RequestContext = Depends(get_current_context)
):
    """Update privacy settings."""
    supabase = get_supabase_client()
    update_data = settings.model_dump(exclude_unset=True)
    
    try:
        res = supabase.table("profiles").update(update_data).eq("id", ctx.user_id).execute()
        log_audit_event(ctx.store_id, ctx.user_id, "update", "privacy_settings", 
                       ctx.user_id, f"Updated settings: {list(update_data.keys())}")
        
        # Return updated settings
        return get_privacy_settings(ctx)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/privacy/sessions", response_model=List[UserSession])
def get_user_sessions(ctx: RequestContext = Depends(get_current_context)):
    """Get all active sessions for the current user."""
    supabase = get_supabase_client()
    res = supabase.table("user_sessions").select("*").eq("user_id", ctx.user_id).order("last_active_at", desc=True).execute()
    return res.data or []


@app.delete("/privacy/sessions/{session_id}", status_code=204)
def revoke_session(
    session_id: str,
    ctx: RequestContext = Depends(get_current_context)
):
    """Revoke/logout a specific session."""
    supabase = get_supabase_client()
    try:
        supabase.table("user_sessions").delete().eq("id", session_id).eq("user_id", ctx.user_id).execute()
        log_audit_event(ctx.store_id, ctx.user_id, "revoke", "session", session_id, "Revoked session")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return None


@app.delete("/privacy/sessions", status_code=204)
def revoke_all_sessions(ctx: RequestContext = Depends(get_current_context)):
    """Revoke all sessions except current one."""
    supabase = get_supabase_client()
    try:
        supabase.table("user_sessions").delete().eq("user_id", ctx.user_id).eq("is_current", False).execute()
        log_audit_event(ctx.store_id, ctx.user_id, "revoke_all", "session", ctx.user_id, "Revoked all other sessions")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return None


@app.post("/privacy/data-export", response_model=DataExportRequest, status_code=201)
def request_data_export(ctx: RequestContext = Depends(get_current_context)):
    """Request an export of all user data (GDPR right to access)."""
    supabase = get_supabase_client()
    
    # Check for pending requests
    pending = supabase.table("data_export_requests").select("*").eq("user_id", ctx.user_id).eq("status", "pending").execute()
    if pending.data:
        raise HTTPException(status_code=400, detail="You already have a pending data export request")
    
    try:
        res = supabase.table("data_export_requests").insert({
            "user_id": ctx.user_id,
            "status": "pending",
            "requested_at": _now_utc_iso()
        }).execute()
        log_audit_event(ctx.store_id, ctx.user_id, "request", "data_export", res.data[0]["id"], "Requested data export")
        return res.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/privacy/data-export", response_model=List[DataExportRequest])
def get_data_export_requests(ctx: RequestContext = Depends(get_current_context)):
    """Get all data export requests for the user."""
    supabase = get_supabase_client()
    res = supabase.table("data_export_requests").select("*").eq("user_id", ctx.user_id).order("requested_at", desc=True).execute()
    return res.data or []


@app.post("/privacy/delete-account", response_model=AccountDeletionRequest, status_code=201)
def request_account_deletion(
    request_data: AccountDeletionCreate,
    ctx: RequestContext = Depends(get_current_context)
):
    """Request account deletion (GDPR right to be forgotten)."""
    supabase = get_supabase_client()
    
    # Check for pending requests
    pending = supabase.table("account_deletion_requests").select("*").eq("user_id", ctx.user_id).in_("status", ["pending", "confirmed"]).execute()
    if pending.data:
        raise HTTPException(status_code=400, detail="You already have a pending deletion request")
    
    # Schedule deletion for 30 days from now
    from datetime import timedelta
    scheduled_date = datetime.now(timezone.utc) + timedelta(days=30)
    
    try:
        res = supabase.table("account_deletion_requests").insert({
            "user_id": ctx.user_id,
            "reason": request_data.reason,
            "status": "pending",
            "requested_at": _now_utc_iso(),
            "scheduled_deletion_at": scheduled_date.isoformat()
        }).execute()
        log_audit_event(ctx.store_id, ctx.user_id, "request", "account_deletion", res.data[0]["id"], "Requested account deletion")
        return res.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/privacy/delete-account/{request_id}", status_code=204)
def cancel_account_deletion(
    request_id: str,
    ctx: RequestContext = Depends(get_current_context)
):
    """Cancel a pending account deletion request."""
    supabase = get_supabase_client()
    
    try:
        res = supabase.table("account_deletion_requests").update({
            "status": "cancelled"
        }).eq("id", request_id).eq("user_id", ctx.user_id).in_("status", ["pending", "confirmed"]).execute()
        
        if not res.data:
            raise HTTPException(status_code=404, detail="Deletion request not found or already processed")
        
        log_audit_event(ctx.store_id, ctx.user_id, "cancel", "account_deletion", request_id, "Cancelled account deletion")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return None


@app.post("/privacy/cookies", response_model=CookiePreferences)
def save_cookie_preferences(
    preferences: CookiePreferences,
    request: Request,
    ctx: RequestContext = Depends(get_current_context)
):
    """Save cookie preferences."""
    supabase = get_supabase_client()
    ip_address = request.client.host if request.client else None
    
    prefs_data = {
        "user_id": ctx.user_id,
        "essential": True,  # Always true
        "analytics": preferences.analytics,
        "marketing": preferences.marketing,
        "functional": preferences.functional,
        "ip_address": ip_address,
        "consented_at": _now_utc_iso()
    }
    
    try:
        res = supabase.table("cookie_preferences").upsert(
            prefs_data,
            on_conflict="user_id"
        ).execute()
        return CookiePreferences(**res.data[0])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/privacy/cookies", response_model=CookiePreferences)
def get_cookie_preferences(ctx: RequestContext = Depends(get_current_context)):
    """Get cookie preferences."""
    supabase = get_supabase_client()
    res = supabase.table("cookie_preferences").select("*").eq("user_id", ctx.user_id).single().execute()
    
    if not res.data:
        return CookiePreferences()
    
    return CookiePreferences(
        essential=True,
        analytics=res.data.get("analytics", False),
        marketing=res.data.get("marketing", False),
        functional=res.data.get("functional", True)
    )


# Legal documents endpoints (return current versions)
@app.get("/legal/terms")
def get_terms_of_service():
    """Get current terms of service."""
    return {
        "version": "1.0",
        "effective_date": "2026-01-01",
        "content_url": "/terms",
        "last_updated": "2026-01-01"
    }


@app.get("/legal/privacy")
def get_privacy_policy():
    """Get current privacy policy."""
    return {
        "version": "1.0",
        "effective_date": "2026-01-01",
        "content_url": "/privacy",
        "last_updated": "2026-01-01"
    }


# Health
class HealthResponse(BaseModel):
    status: str
    time: str


@app.get("/health", response_model=HealthResponse)
def health():
    return {"status": "ok", "time": _now_utc_iso()}


