from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException

import app.db.supabase as supabase_client
from app.api.deps import RequestContext


def report_day_bounds(date_str: Optional[str], tz_name: Optional[str]):
    """
    Return (day_start_utc_iso, day_end_utc_iso, target_date) for the given date.
    If tz_name is provided (e.g. 'Africa/Johannesburg'), the date is interpreted as that
    timezone's calendar day; otherwise it is treated as UTC. Defaults to today UTC if date_str is None.
    """
    if date_str:
        try:
            target_date = datetime.fromisoformat(date_str).date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    else:
        target_date = datetime.now(timezone.utc).date()

    tz = None
    if tz_name:
        try:
            from zoneinfo import ZoneInfo

            tz = ZoneInfo(tz_name)
        except ImportError:
            raise HTTPException(status_code=500, detail="Timezone support requires Python 3.9+ (zoneinfo)")
        except Exception:
            import logging

            logging.getLogger(__name__).warning(
                "Timezone %r not available, using UTC. On Windows install: pip install tzdata", tz_name
            )

    if tz is not None:
        start_local = datetime.combine(target_date, datetime.min.time(), tzinfo=tz)
        end_local = datetime.combine(target_date, datetime.max.time(), tzinfo=tz)
        day_start = start_local.astimezone(timezone.utc).isoformat()
        day_end = end_local.astimezone(timezone.utc).isoformat()
    else:
        day_start = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc).isoformat()
        day_end = datetime.combine(target_date, datetime.max.time(), tzinfo=timezone.utc).isoformat()

    return day_start, day_end, target_date


def build_daily_report(
    ctx: RequestContext, date_utc: Optional[str] = None, timezone_name: Optional[str] = None
):
    """
    Returns (target_date, totals, transactions) for the given date (YYYY-MM-DD).
    If timezone_name is set, the date is the calendar day in that timezone; otherwise UTC.
    """
    supabase = supabase_client.get_supabase_client()
    day_start, day_end, target_date = report_day_bounds(date_utc, timezone_name)

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

    try:
        products_res = supabase.table("products").select("id,name,image_url,cost_price").eq("store_id", ctx.store_id).execute()
        cost_by_id = {}
        product_info_by_id = {}
        for p in products_res.data or []:
            pid = int(p["id"])
            cost_val = p.get("cost_price")
            if cost_val is None:
                cost_by_id[pid] = 0.0
            else:
                try:
                    cost_by_id[pid] = float(cost_val)
                except (ValueError, TypeError):
                    cost_by_id[pid] = 0.0
            product_info_by_id[pid] = {"name": p.get("name") or f"Product #{pid}", "image_url": p.get("image_url")}
    except Exception:
        cost_by_id = {}
        product_info_by_id = {}
    total_profit = 0.0
    for tx in transactions:
        pid = int(tx["product_id"])
        qty = int(tx["quantity_sold"])
        cost = cost_by_id.get(pid, 0.0)
        revenue = float(tx["total_price"])
        profit = revenue - (cost * qty)
        total_profit += profit
        tx["profit"] = round(float(profit), 2)
        info = product_info_by_id.get(pid, {})
        tx["product_name"] = info.get("name", f"Product #{pid}")
        tx["product_image_url"] = info.get("image_url")

    totals = {
        "total_sales_count": total_sales_count,
        "total_revenue": total_revenue,
        "total_profit": total_profit,
    }
    return target_date, totals, transactions
