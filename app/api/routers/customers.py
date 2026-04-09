from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException

import app.services.audit_log as audit_log
import app.services.subscriptions as subscriptions
import app.db.supabase as supabase_client
from app.api.deps import RequestContext, get_current_context
from app.schemas import Customer, CustomerCreate, CustomerUpdate, Sale

router = APIRouter()


@router.get("/customers", response_model=List[Customer])
def list_customers(
    q: Optional[str] = None,
    include_inactive: bool = False,
    ctx: RequestContext = Depends(get_current_context),
):
    limits = subscriptions.get_store_plan(ctx.store_id)
    if not limits.is_active:
        raise HTTPException(status_code=402, detail="Customers require an active Pro plan")
    supabase = supabase_client.get_supabase_client()
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


@router.get("/customers/{customer_id}", response_model=Customer)
def get_customer(customer_id: str, ctx: RequestContext = Depends(get_current_context)):
    limits = subscriptions.get_store_plan(ctx.store_id)
    if not limits.is_active:
        raise HTTPException(status_code=402, detail="Customers require an active Pro plan")
    supabase = supabase_client.get_supabase_client()
    try:
        res = supabase.table("customers").select("*").eq("id", customer_id).eq("store_id", ctx.store_id).single().execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Customer not found")
        return res.data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/customers/{customer_id}/purchases", response_model=List[Sale])
def get_customer_purchases(customer_id: str, limit: int = 50, ctx: RequestContext = Depends(get_current_context)):
    limits = subscriptions.get_store_plan(ctx.store_id)
    if not limits.is_active:
        raise HTTPException(status_code=402, detail="Customers require an active Pro plan")
    supabase = supabase_client.get_supabase_client()
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


@router.post("/customers", response_model=Customer, status_code=201)
def create_customer(payload: CustomerCreate, ctx: RequestContext = Depends(get_current_context)):
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    limits = subscriptions.get_store_plan(ctx.store_id)
    if not limits.is_active:
        raise HTTPException(status_code=402, detail="Customers require an active Pro plan")
    supabase = supabase_client.get_supabase_client()
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
            audit_log.log_audit_event(
                ctx.store_id, ctx.user_id, "create", "customer", str(res.data[0]["id"]), f"Created customer: {payload.name}"
            )
            return res.data[0]
        raise HTTPException(status_code=500, detail="Failed to create customer")
    except Exception as e:
        if "duplicate key" in str(e).lower() or "unique" in str(e).lower():
            raise HTTPException(status_code=400, detail="Customer with this email or phone already exists")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/customers/{customer_id}", response_model=Customer)
def update_customer(customer_id: str, payload: CustomerUpdate, ctx: RequestContext = Depends(get_current_context)):
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    limits = subscriptions.get_store_plan(ctx.store_id)
    if not limits.is_active:
        raise HTTPException(status_code=402, detail="Customers require an active Pro plan")
    supabase = supabase_client.get_supabase_client()
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
            audit_log.log_audit_event(
                ctx.store_id, ctx.user_id, "update", "customer", customer_id, f"Updated customer: {list(update_data.keys())}"
            )
            return res.data[0]
        raise HTTPException(status_code=404, detail="Customer not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/customers/{customer_id}", status_code=204)
def delete_customer(customer_id: str, ctx: RequestContext = Depends(get_current_context)):
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    supabase = supabase_client.get_supabase_client()
    try:
        supabase.table("customers").delete().eq("id", customer_id).eq("store_id", ctx.store_id).execute()
        audit_log.log_audit_event(ctx.store_id, ctx.user_id, "delete", "customer", customer_id, "Deleted customer")
        return None
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/customers/{customer_id}/add-points", response_model=Customer)
def add_loyalty_points(customer_id: str, points: int, ctx: RequestContext = Depends(get_current_context)):
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    supabase = supabase_client.get_supabase_client()
    try:
        existing = (
            supabase.table("customers")
            .select("loyalty_points")
            .eq("id", customer_id)
            .eq("store_id", ctx.store_id)
            .single()
            .execute()
        )
        if not existing.data:
            raise HTTPException(status_code=404, detail="Customer not found")

        new_points = (existing.data.get("loyalty_points") or 0) + points
        res = (
            supabase.table("customers")
            .update({"loyalty_points": new_points})
            .eq("id", customer_id)
            .eq("store_id", ctx.store_id)
            .execute()
        )

        if res.data and len(res.data) > 0:
            return res.data[0]
        raise HTTPException(status_code=500, detail="Failed to update points")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
