import csv
import io
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response

import app.services.subscriptions as subscriptions
import app.db.supabase as supabase_client
from app.api.deps import RequestContext, get_current_context
from app.services.reporting import build_daily_report, report_day_bounds
from app.schemas import (
    InventoryValuationReport,
    ProfitLossReport,
    ReportResponse,
    TaxReport,
)

router = APIRouter()


@router.get("/reports", response_model=ReportResponse)
def get_reports(
    date_utc: Optional[str] = None,
    tz: Optional[str] = Query(None, alias="timezone"),
    ctx: RequestContext = Depends(get_current_context),
):
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    limits = subscriptions.get_store_plan(ctx.store_id)
    if not limits.allow_advanced_reports:
        raise HTTPException(status_code=402, detail="Reports & analytics require Pro plan")
    _, totals, transactions = build_daily_report(ctx, date_utc, timezone_name=tz)
    return {
        "totals": totals,
        "transactions": transactions,
    }


@router.get("/reports/export")
def export_reports_csv(
    date_utc: Optional[str] = None,
    tz: Optional[str] = Query(None, alias="timezone"),
    ctx: RequestContext = Depends(get_current_context),
):
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    limits = subscriptions.get_store_plan(ctx.store_id)
    if not limits.allow_csv_export:
        raise HTTPException(status_code=402, detail="CSV export requires Pro or Business plan")

    day_start, day_end, target_date = report_day_bounds(date_utc, tz)
    supabase = supabase_client.get_supabase_client()

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
        writer.writerow(
            [
                tx["id"],
                tx["product_id"],
                product_info["sku"],
                product_info["name"],
                tx["quantity_sold"],
                tx["total_price"],
                tx["timestamp"],
            ]
        )

    csv_content = output.getvalue()
    output.close()

    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=sales_report_{target_date}.csv"},
    )


@router.get("/reports/profit-loss", response_model=ProfitLossReport)
def get_profit_loss_report(
    start_date: date,
    end_date: date,
    ctx: RequestContext = Depends(get_current_context),
):
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    limits = subscriptions.get_store_plan(ctx.store_id)
    if not limits.allow_advanced_reports:
        raise HTTPException(status_code=402, detail="Reports & analytics require Pro plan")

    supabase = supabase_client.get_supabase_client()

    sales_res = (
        supabase.table("sales")
        .select("*")
        .eq("store_id", ctx.store_id)
        .gte("timestamp", f"{start_date.isoformat()}T00:00:00Z")
        .lte("timestamp", f"{end_date.isoformat()}T23:59:59Z")
        .execute()
    )
    sales = sales_res.data or []

    expenses_res = (
        supabase.table("expenses")
        .select("*")
        .eq("store_id", ctx.store_id)
        .gte("expense_date", start_date.isoformat())
        .lte("expense_date", end_date.isoformat())
        .execute()
    )
    expenses = expenses_res.data or []

    total_revenue = sum(float(s.get("total_price", 0)) for s in sales)
    total_profit_from_sales = sum(float(s.get("profit", 0) or 0) for s in sales)
    total_cost_of_goods = total_revenue - total_profit_from_sales

    total_expenses = sum(float(e.get("amount", 0)) for e in expenses)
    net_profit = total_profit_from_sales - total_expenses

    expense_by_cat = {}
    for e in expenses:
        cat = e.get("category", "Other")
        expense_by_cat[cat] = expense_by_cat.get(cat, 0) + float(e.get("amount", 0))
    expense_breakdown = [{"category": k, "amount": v} for k, v in sorted(expense_by_cat.items(), key=lambda x: -x[1])]

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
        revenue_by_day=sorted(revenue_by_day.values(), key=lambda x: x["date"]),
    )


@router.get("/reports/tax", response_model=TaxReport)
def get_tax_report(
    start_date: date,
    end_date: date,
    tax_rate: float = 15.0,
    ctx: RequestContext = Depends(get_current_context),
):
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    limits = subscriptions.get_store_plan(ctx.store_id)
    if not limits.allow_advanced_reports:
        raise HTTPException(status_code=402, detail="Reports & analytics require Pro plan")

    supabase = supabase_client.get_supabase_client()

    sales_res = (
        supabase.table("sales")
        .select("*")
        .eq("store_id", ctx.store_id)
        .gte("timestamp", f"{start_date.isoformat()}T00:00:00Z")
        .lte("timestamp", f"{end_date.isoformat()}T23:59:59Z")
        .execute()
    )
    sales = sales_res.data or []

    total_sales = sum(float(s.get("total_price", 0)) for s in sales)

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
        transactions_count=len(sales),
    )


@router.get("/reports/inventory-valuation", response_model=InventoryValuationReport)
def get_inventory_valuation_report(
    low_stock_threshold: int = 10,
    ctx: RequestContext = Depends(get_current_context),
):
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    limits = subscriptions.get_store_plan(ctx.store_id)
    if not limits.allow_advanced_reports:
        raise HTTPException(status_code=402, detail="Reports & analytics require Pro plan")

    supabase = supabase_client.get_supabase_client()

    products_res = supabase.table("products").select("*").eq("store_id", ctx.store_id).execute()
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

        cat_name = "All Products"
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
        categories=sorted(by_category.values(), key=lambda x: -x["retail_value"]),
    )


@router.get("/reports/export/profit-loss")
def export_profit_loss_csv(
    start_date: date,
    end_date: date,
    ctx: RequestContext = Depends(get_current_context),
):
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    limits = subscriptions.get_store_plan(ctx.store_id)
    if not limits.allow_advanced_reports:
        raise HTTPException(status_code=402, detail="Reports & analytics require Pro plan")

    report = get_profit_loss_report(start_date, end_date, ctx)

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["Profit & Loss Report"])
    writer.writerow([f"Period: {start_date} to {end_date}"])
    writer.writerow([])

    writer.writerow(["Summary"])
    writer.writerow(["Total Revenue", f"R{report.total_revenue:,.2f}"])
    writer.writerow(["Cost of Goods Sold", f"R{report.total_cost_of_goods:,.2f}"])
    writer.writerow(["Gross Profit", f"R{report.gross_profit:,.2f}"])
    writer.writerow(["Total Expenses", f"R{report.total_expenses:,.2f}"])
    writer.writerow(["Net Profit", f"R{report.net_profit:,.2f}"])
    writer.writerow([])

    writer.writerow(["Expense Breakdown"])
    writer.writerow(["Category", "Amount"])
    for e in report.expense_breakdown:
        writer.writerow([e["category"], f"R{e['amount']:,.2f}"])
    writer.writerow([])

    writer.writerow(["Daily Revenue & Profit"])
    writer.writerow(["Date", "Revenue", "Profit"])
    for d in report.revenue_by_day:
        writer.writerow([d["date"], f"R{d['revenue']:,.2f}", f"R{d['profit']:,.2f}"])

    content = output.getvalue()
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=profit_loss_{start_date}_{end_date}.csv"},
    )
