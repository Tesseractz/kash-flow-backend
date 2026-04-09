from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException

import app.services.audit_log as audit_log
import app.services.subscriptions as subscriptions
import app.db.supabase as supabase_client
from app.api.deps import RequestContext, get_current_context
from app.schemas import Expense, ExpenseCategory, ExpenseCreate, ExpenseUpdate

router = APIRouter()


@router.get("/expenses", response_model=List[Expense])
def list_expenses(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    category: Optional[str] = None,
    ctx: RequestContext = Depends(get_current_context),
):
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    limits = subscriptions.get_store_plan(ctx.store_id)
    if not limits.is_active:
        raise HTTPException(status_code=402, detail="Expenses require an active Pro plan")

    supabase = supabase_client.get_supabase_client()
    query = supabase.table("expenses").select("*").eq("store_id", ctx.store_id)

    if start_date:
        query = query.gte("expense_date", start_date.isoformat())
    if end_date:
        query = query.lte("expense_date", end_date.isoformat())
    if category:
        query = query.eq("category", category)

    res = query.order("expense_date", desc=True).execute()
    return res.data or []


@router.post("/expenses", response_model=Expense, status_code=201)
def create_expense(payload: ExpenseCreate, ctx: RequestContext = Depends(get_current_context)):
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    limits = subscriptions.get_store_plan(ctx.store_id)
    if not limits.is_active:
        raise HTTPException(status_code=402, detail="Expenses require an active Pro plan")

    supabase = supabase_client.get_supabase_client()

    expense_data = payload.model_dump()
    expense_data["store_id"] = ctx.store_id
    expense_data["user_id"] = ctx.user_id
    expense_data["expense_date"] = expense_data["expense_date"].isoformat()

    try:
        res = supabase.table("expenses").insert(expense_data).execute()
        audit_log.log_audit_event(
            ctx.store_id,
            ctx.user_id,
            "create",
            "expense",
            res.data[0]["id"],
            f"Created expense: {payload.category} - {payload.amount}",
        )
        return res.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/expenses/{expense_id}", response_model=Expense)
def get_expense(expense_id: str, ctx: RequestContext = Depends(get_current_context)):
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")

    supabase = supabase_client.get_supabase_client()
    res = supabase.table("expenses").select("*").eq("id", expense_id).eq("store_id", ctx.store_id).single().execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Expense not found")
    return res.data


@router.put("/expenses/{expense_id}", response_model=Expense)
def update_expense(expense_id: str, payload: ExpenseUpdate, ctx: RequestContext = Depends(get_current_context)):
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")

    supabase = supabase_client.get_supabase_client()
    update_data = payload.model_dump(exclude_unset=True)

    if "expense_date" in update_data and update_data["expense_date"]:
        update_data["expense_date"] = update_data["expense_date"].isoformat()

    try:
        res = supabase.table("expenses").update(update_data).eq("id", expense_id).eq("store_id", ctx.store_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Expense not found")
        audit_log.log_audit_event(
            ctx.store_id,
            ctx.user_id,
            "update",
            "expense",
            expense_id,
            f"Updated expense fields: {list(update_data.keys())}",
        )
        return res.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/expenses/{expense_id}", status_code=204)
def delete_expense(expense_id: str, ctx: RequestContext = Depends(get_current_context)):
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")

    supabase = supabase_client.get_supabase_client()
    try:
        supabase.table("expenses").delete().eq("id", expense_id).eq("store_id", ctx.store_id).execute()
        audit_log.log_audit_event(ctx.store_id, ctx.user_id, "delete", "expense", expense_id, "Deleted expense")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return None


@router.get("/expense-categories", response_model=List[ExpenseCategory])
def list_expense_categories(ctx: RequestContext = Depends(get_current_context)):
    supabase = supabase_client.get_supabase_client()
    res = supabase.table("expense_categories").select("*").eq("store_id", ctx.store_id).order("name").execute()

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
        for cat in default_categories:
            cat["store_id"] = ctx.store_id
            cat["is_system"] = True
        supabase.table("expense_categories").insert(default_categories).execute()
        res = supabase.table("expense_categories").select("*").eq("store_id", ctx.store_id).order("name").execute()

    return res.data or []


@router.get("/expenses/summary")
def get_expense_summary(start_date: date, end_date: date, ctx: RequestContext = Depends(get_current_context)):
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")

    supabase = supabase_client.get_supabase_client()

    expenses_res = (
        supabase.table("expenses")
        .select("*")
        .eq("store_id", ctx.store_id)
        .gte("expense_date", start_date.isoformat())
        .lte("expense_date", end_date.isoformat())
        .execute()
    )
    expenses = expenses_res.data or []

    total_amount = sum(float(e.get("amount", 0)) for e in expenses)

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
        "category_breakdown": category_breakdown,
    }
