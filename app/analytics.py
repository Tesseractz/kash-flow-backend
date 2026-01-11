"""
Analytics module for detailed sales insights.
Provides data for charts, trends, and business intelligence.
"""

from datetime import datetime, timedelta, timezone, date
from typing import List, Optional
from pydantic import BaseModel
from .supabase_client import get_supabase_client


class SalesTrend(BaseModel):
    date: str
    revenue: float
    profit: float
    sales_count: int


class TopProduct(BaseModel):
    product_id: int
    name: str
    sku: Optional[str] = None
    total_sold: int
    total_revenue: float
    total_profit: float


class HourlySales(BaseModel):
    hour: int
    sales_count: int
    revenue: float


class AnalyticsSummary(BaseModel):
    period_days: int
    total_revenue: float
    total_profit: float
    total_sales: int
    avg_transaction_value: float
    profit_margin: float
    best_day: Optional[str] = None
    best_day_revenue: float = 0
    worst_day: Optional[str] = None
    worst_day_revenue: float = 0
    revenue_trend: float = 0
    sales_trends: List[SalesTrend] = []
    top_products: List[TopProduct] = []
    hourly_breakdown: List[HourlySales] = []


def get_analytics(store_id: str, days: int = 30) -> AnalyticsSummary:
    """
    Get comprehensive analytics for a store.
    
    Args:
        store_id: The store ID
        days: Number of days to analyze (default 30)
    
    Returns:
        AnalyticsSummary with trends, top products, and insights
    """
    supabase = get_supabase_client()
    
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=days)
    
    try:
        sales_res = supabase.table("sales").select(
            "id, product_id, quantity_sold, total_price, timestamp"
        ).eq("store_id", store_id).gte(
            "timestamp", start_date.isoformat()
        ).lte(
            "timestamp", end_date.isoformat()
        ).execute()
        
        sales = sales_res.data or []
    except Exception:
        sales = []
    
    try:
        products_res = supabase.table("products").select(
            "id, name, sku, price, cost_price"
        ).eq("store_id", store_id).execute()
        
        products = {p["id"]: p for p in (products_res.data or [])}
    except Exception:
        products = {}
    
    if not sales:
        return AnalyticsSummary(
            period_days=days,
            total_revenue=0,
            total_profit=0,
            total_sales=0,
            avg_transaction_value=0,
            profit_margin=0
        )
    
    total_revenue = sum(float(s.get("total_price", 0)) for s in sales)
    total_sales = len(sales)
    
    total_profit = 0
    for s in sales:
        product = products.get(s.get("product_id"))
        if product:
            price = float(product.get("price", 0))
            cost = float(product.get("cost_price", 0))
            qty = int(s.get("quantity_sold", 0))
            total_profit += (price - cost) * qty
    
    avg_transaction = total_revenue / total_sales if total_sales > 0 else 0
    profit_margin = (total_profit / total_revenue * 100) if total_revenue > 0 else 0
    
    daily_data = {}
    for s in sales:
        try:
            ts = s.get("timestamp", "")
            if "T" in ts:
                day = ts.split("T")[0]
            else:
                day = ts[:10]
            
            if day not in daily_data:
                daily_data[day] = {"revenue": 0, "profit": 0, "count": 0}
            
            revenue = float(s.get("total_price", 0))
            daily_data[day]["revenue"] += revenue
            daily_data[day]["count"] += 1
            
            product = products.get(s.get("product_id"))
            if product:
                price = float(product.get("price", 0))
                cost = float(product.get("cost_price", 0))
                qty = int(s.get("quantity_sold", 0))
                daily_data[day]["profit"] += (price - cost) * qty
        except Exception:
            continue
    
    sales_trends = []
    for day in sorted(daily_data.keys()):
        data = daily_data[day]
        sales_trends.append(SalesTrend(
            date=day,
            revenue=round(data["revenue"], 2),
            profit=round(data["profit"], 2),
            sales_count=data["count"]
        ))
    
    best_day = max(daily_data.items(), key=lambda x: x[1]["revenue"]) if daily_data else (None, {"revenue": 0})
    worst_day = min(daily_data.items(), key=lambda x: x[1]["revenue"]) if daily_data else (None, {"revenue": 0})
    
    revenue_trend = 0
    if len(sales_trends) >= 2:
        first_half = sales_trends[:len(sales_trends)//2]
        second_half = sales_trends[len(sales_trends)//2:]
        first_avg = sum(t.revenue for t in first_half) / len(first_half) if first_half else 0
        second_avg = sum(t.revenue for t in second_half) / len(second_half) if second_half else 0
        if first_avg > 0:
            revenue_trend = ((second_avg - first_avg) / first_avg) * 100
    
    product_stats = {}
    for s in sales:
        pid = s.get("product_id")
        if pid not in product_stats:
            product_stats[pid] = {"sold": 0, "revenue": 0, "profit": 0}
        
        qty = int(s.get("quantity_sold", 0))
        rev = float(s.get("total_price", 0))
        product_stats[pid]["sold"] += qty
        product_stats[pid]["revenue"] += rev
        
        product = products.get(pid)
        if product:
            price = float(product.get("price", 0))
            cost = float(product.get("cost_price", 0))
            product_stats[pid]["profit"] += (price - cost) * qty
    
    top_products = []
    sorted_products = sorted(product_stats.items(), key=lambda x: x[1]["revenue"], reverse=True)[:10]
    for pid, stats in sorted_products:
        product = products.get(pid, {})
        top_products.append(TopProduct(
            product_id=pid,
            name=product.get("name", f"Product #{pid}"),
            sku=product.get("sku"),
            total_sold=stats["sold"],
            total_revenue=round(stats["revenue"], 2),
            total_profit=round(stats["profit"], 2)
        ))
    
    hourly_data = {h: {"count": 0, "revenue": 0} for h in range(24)}
    for s in sales:
        try:
            ts = s.get("timestamp", "")
            if "T" in ts:
                hour = int(ts.split("T")[1][:2])
                hourly_data[hour]["count"] += 1
                hourly_data[hour]["revenue"] += float(s.get("total_price", 0))
        except Exception:
            continue
    
    hourly_breakdown = [
        HourlySales(
            hour=h,
            sales_count=data["count"],
            revenue=round(data["revenue"], 2)
        )
        for h, data in sorted(hourly_data.items())
    ]
    
    return AnalyticsSummary(
        period_days=days,
        total_revenue=round(total_revenue, 2),
        total_profit=round(total_profit, 2),
        total_sales=total_sales,
        avg_transaction_value=round(avg_transaction, 2),
        profit_margin=round(profit_margin, 1),
        best_day=best_day[0] if best_day[0] else None,
        best_day_revenue=round(best_day[1]["revenue"], 2) if best_day[0] else 0,
        worst_day=worst_day[0] if worst_day[0] else None,
        worst_day_revenue=round(worst_day[1]["revenue"], 2) if worst_day[0] else 0,
        revenue_trend=round(revenue_trend, 1),
        sales_trends=sales_trends,
        top_products=top_products,
        hourly_breakdown=hourly_breakdown
    )
