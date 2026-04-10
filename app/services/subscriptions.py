import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException

from app.db.supabase import get_supabase_client

DEV_PLAN_OVERRIDE = os.getenv("DEV_PLAN_OVERRIDE", "").lower()


class PlanLimits:
    def __init__(self, plan: str, status: str = "active", trial_end: Optional[str] = None):
        normalized_plan = "pro" if plan == "business" else plan
        self.plan = normalized_plan
        st = status
        if normalized_plan in ("pro", "business") and st == "trialing" and not trial_end:
            st = "active"
        self.status = st
        self.trial_end = trial_end
        self._is_trial_active = self._check_trial_active()

    def _check_trial_active(self) -> bool:
        if self.status != "trialing":
            return False
        if not self.trial_end:
            return False
        try:
            trial_dt = datetime.fromisoformat(self.trial_end.replace("Z", "+00:00"))
            return datetime.now(timezone.utc) < trial_dt
        except Exception:
            return False

    @property
    def is_active(self) -> bool:
        if self.status == "active" and self.plan in ("pro", "business"):
            return True
        if self.status == "trialing" and self._is_trial_active:
            return True
        return False

    @property
    def is_on_trial(self) -> bool:
        return self.status == "trialing" and self._is_trial_active

    @property
    def max_products(self) -> Optional[int]:
        if self.is_on_trial:
            return None
        if self.is_active and self.plan in ("pro", "business"):
            return None
        return 10

    @property
    def max_users(self) -> int:
        if self.is_on_trial:
            return 999
        if self.is_active and self.plan in ("pro", "business"):
            return 999
        return 1

    @property
    def allow_multiple_users(self) -> bool:
        if self.is_on_trial:
            return True
        return self.is_active and self.plan in ("pro", "business")

    @property
    def allow_csv_export(self) -> bool:
        if self.is_on_trial:
            return True
        return self.is_active and self.plan in ("pro", "business")

    @property
    def allow_low_stock_alerts(self) -> bool:
        if self.is_on_trial:
            return True
        return self.is_active and self.plan in ("pro", "business")

    @property
    def allow_audit_logs(self) -> bool:
        if self.is_on_trial:
            return True
        return self.is_active and self.plan in ("pro", "business")

    @property
    def allow_advanced_reports(self) -> bool:
        if self.is_on_trial:
            return True
        return self.is_active and self.plan in ("pro", "business")


def get_store_plan(store_id: str) -> PlanLimits:
    if DEV_PLAN_OVERRIDE in ("pro", "business"):
        normalized = "pro" if DEV_PLAN_OVERRIDE == "business" else DEV_PLAN_OVERRIDE
        return PlanLimits(normalized, status="active")

    supa = get_supabase_client()
    try:
        res = supa.table("subscriptions").select("*").eq("store_id", store_id).single().execute()
        data = res.data or {}
        plan = data.get("plan", "expired")
        status = data.get("status", "expired")
        trial_end = data.get("trial_end")
        # One trial per store: if already consumed, do not grant in-app trial perks even if Paystack sends trialing again.
        if data.get("trial_consumed_at") and status == "trialing":
            status = "active"
            trial_end = None

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
        "is_on_trial": limits.is_on_trial,
        "trial_end": sub_data.get("trial_end"),
        "current_period_end": sub_data.get("current_period_end"),
        "trial_consumed": bool(sub_data.get("trial_consumed_at")),
        "has_paystack_subscription": bool(sub_data.get("paystack_subscription_code")),
        "billing_provider": sub_data.get("billing_provider"),
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
        },
    }
