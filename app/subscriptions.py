import os
from datetime import datetime, timezone
from typing import Optional
from fastapi import HTTPException
from .supabase_client import get_supabase_client

DEV_PLAN_OVERRIDE = os.getenv("DEV_PLAN_OVERRIDE", "").lower()


class PlanLimits:
    def __init__(self, plan: str, status: str = "active", trial_end: Optional[str] = None):
        self.plan = plan
        self.status = status
        self.trial_end = trial_end
        self._is_trial_active = self._check_trial_active()

    def _check_trial_active(self) -> bool:
        """Check if trial is still active."""
        if self.status != "trialing":
            return False
        if not self.trial_end:
            return True
        try:
            trial_dt = datetime.fromisoformat(self.trial_end.replace("Z", "+00:00"))
            return datetime.now(timezone.utc) < trial_dt
        except Exception:
            return False

    @property
    def is_active(self) -> bool:
        """Check if subscription is active (paid or valid trial)."""
        if self.status == "active":
            return True
        if self.status == "trialing" and self._is_trial_active:
            return True
        return False

    @property
    def max_products(self) -> Optional[int]:
        if not self.is_active:
            return 10
        return None

    @property
    def max_users(self) -> int:
        if not self.is_active:
            return 1
        elif self.plan == "pro":
            return 3
        return 999

    @property
    def allow_multiple_users(self) -> bool:
        return self.is_active and self.plan == "business"

    @property
    def allow_csv_export(self) -> bool:
        return self.is_active and self.plan in ("pro", "business")

    @property
    def allow_low_stock_alerts(self) -> bool:
        return self.is_active and self.plan in ("pro", "business")

    @property
    def allow_audit_logs(self) -> bool:
        return self.is_active and self.plan == "business"

    @property
    def allow_advanced_reports(self) -> bool:
        return self.is_active and self.plan in ("pro", "business")


def get_store_plan(store_id: str) -> PlanLimits:
    if DEV_PLAN_OVERRIDE in ("pro", "business"):
        return PlanLimits(DEV_PLAN_OVERRIDE, status="active")
    
    supa = get_supabase_client()
    try:
        res = supa.table("subscriptions").select("*").eq("store_id", store_id).single().execute()
        data = res.data or {}
        plan = data.get("plan", "expired")
        status = data.get("status", "expired")
        trial_end = data.get("trial_end")
        
        return PlanLimits(plan, status=status, trial_end=trial_end)
    except Exception:
        return PlanLimits("expired", status="expired")


def enforce_limits_on_create_product(store_id: str):
    limits = get_store_plan(store_id)
    max_products = limits.max_products
    if max_products is None:
        return
    supa = get_supabase_client()
    count = supa.table("products").select("id").eq("store_id", store_id).execute()
    total = len(count.data or [])
    if total >= max_products:
        raise HTTPException(status_code=402, detail="Product limit reached for current plan")


def get_plan_info(store_id: str) -> dict:
    limits = get_store_plan(store_id)
    supa = get_supabase_client()
    
    product_count = len((supa.table("products").select("id").eq("store_id", store_id).execute()).data or [])
    
    try:
        sub_res = supa.table("subscriptions").select("*").eq("store_id", store_id).single().execute()
        sub_data = sub_res.data or {}
    except Exception:
        sub_data = {}
    
    return {
        "plan": limits.plan,
        "status": limits.status,
        "is_active": limits.is_active,
        "trial_end": sub_data.get("trial_end"),
        "current_period_end": sub_data.get("current_period_end"),
        "has_stripe_subscription": bool(sub_data.get("stripe_subscription_id")),
        "limits": {
            "max_products": limits.max_products,
            "max_users": limits.max_users,
            "csv_export": limits.allow_csv_export,
            "low_stock_alerts": limits.allow_low_stock_alerts,
            "audit_logs": limits.allow_audit_logs,
            "advanced_reports": limits.allow_advanced_reports,
        },
        "usage": {
            "products": product_count,
        }
    }
