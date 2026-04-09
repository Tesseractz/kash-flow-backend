import base64
from io import BytesIO
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

import app.services.audit_log as audit_log
import app.services.notification_settings as notification_settings
import app.services.push as push_mod
import app.services.subscriptions as subscriptions
import app.db.supabase as supabase_client
from app.api.deps import RequestContext, get_current_context
from app.schemas import (
    BarcodeGenerateRequest,
    BarcodeLookupResponse,
    BarcodeResponse,
    Product,
    ProductCreate,
    ProductUpdate,
    Sale,
    SaleCreate,
)
from app.core.time_utils import now_utc_iso

router = APIRouter()


@router.get("/products", response_model=List[Product])
def list_products(
    response: Response,
    q: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    page: Optional[int] = None,
    page_size: Optional[int] = None,
    ctx: RequestContext = Depends(get_current_context),
):
    supabase = supabase_client.get_supabase_client()
    try:
        query = supabase.table("products").select("*").eq("store_id", ctx.store_id)
        if q:
            conds = [f"name.ilike.%{q}%", f"sku.ilike.%{q}%"]
            if q.isdigit():
                conds.append(f"id.eq.{int(q)}")
            query = query.or_(",".join(conds))
        if min_price is not None:
            query = query.gte("price", min_price)
        if max_price is not None:
            query = query.lte("price", max_price)

        query = query.order("id")

        total = None
        if page and page_size:
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


@router.post("/products", response_model=Product, status_code=201)
def create_product(payload: ProductCreate, ctx: RequestContext = Depends(get_current_context)):
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    subscriptions.enforce_limits_on_create_product(ctx.store_id)
    supabase = supabase_client.get_supabase_client()
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
            audit_log.log_audit_event(
                ctx.store_id, ctx.user_id, "create", "product", str(product["id"]), f"Created product: {payload.name}"
            )
            return product
        raise HTTPException(status_code=500, detail="Insert succeeded but no data returned")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/products/{product_id}", response_model=Product)
def update_product(product_id: int, payload: ProductUpdate, ctx: RequestContext = Depends(get_current_context)):
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    supabase = supabase_client.get_supabase_client()
    update_data = {k: v for k, v in payload.model_dump(exclude_unset=True).items()}
    if not update_data:
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

        audit_log.log_audit_event(
            ctx.store_id,
            ctx.user_id,
            "update",
            "product",
            str(product_id),
            f"Updated product fields: {list(update_data.keys())}",
        )
        try:
            settings = notification_settings.fetch_notification_settings(supabase, ctx.store_id)
            threshold = int(settings.get("low_stock_threshold") or 10)
            qty = int(product.get("quantity") or 0)
            if qty <= threshold:
                subs = (
                    supabase.table("push_subscriptions")
                    .select("endpoint,p256dh,auth")
                    .eq("store_id", ctx.store_id)
                    .execute()
                ).data or []
                if subs:
                    push_mod.send_web_push(
                        subs,
                        title=f"Low stock: {product.get('name') or 'Product'}",
                        body=f"Only {qty} left in stock.",
                        url="/products",
                    )
        except Exception:
            pass
        return product
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/products/{product_id}", status_code=204)
def delete_product(product_id: int, ctx: RequestContext = Depends(get_current_context)):
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    supabase = supabase_client.get_supabase_client()
    try:
        supabase.table("products").delete().eq("id", product_id).eq("store_id", ctx.store_id).execute()
        audit_log.log_audit_event(ctx.store_id, ctx.user_id, "delete", "product", str(product_id), "Deleted product")
        return None
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sales", response_model=List[Sale])
def list_sales(ctx: RequestContext = Depends(get_current_context)):
    supabase = supabase_client.get_supabase_client()
    try:
        res = supabase.table("sales").select("*").eq("store_id", ctx.store_id).order("timestamp", desc=True).execute()
        return res.data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sales", response_model=Sale, status_code=201)
def create_sale(payload: SaleCreate, ctx: RequestContext = Depends(get_current_context)):
    supabase = supabase_client.get_supabase_client()
    try:
        rpc = supabase.rpc(
            "process_sale",
            {
                "p_store_id": ctx.store_id,
                "p_product_id": payload.product_id,
                "p_qty": payload.quantity_sold,
                "p_sold_by": ctx.user_id,
            },
        ).execute()
        sale = None
        if isinstance(rpc.data, list) and len(rpc.data) > 0:
            sale = rpc.data[0]
        elif isinstance(rpc.data, dict):
            sale = rpc.data

        if sale:
            audit_log.log_audit_event(
                ctx.store_id,
                ctx.user_id,
                "create",
                "sale",
                str(sale.get("id", "")),
                f"Sale: product {payload.product_id}, qty {payload.quantity_sold}",
            )
            try:
                settings = notification_settings.fetch_notification_settings(supabase, ctx.store_id)
                threshold = int(settings.get("low_stock_threshold") or 10)
                prod_res = (
                    supabase.table("products")
                    .select("id,name,quantity")
                    .eq("store_id", ctx.store_id)
                    .eq("id", payload.product_id)
                    .single()
                    .execute()
                )
                p = prod_res.data or {}
                qty = int(p.get("quantity") or 0)
                if qty <= threshold:
                    subs = (
                        supabase.table("push_subscriptions")
                        .select("endpoint,p256dh,auth")
                        .eq("store_id", ctx.store_id)
                        .execute()
                    ).data or []
                    if subs:
                        push_mod.send_web_push(
                            subs,
                            title=f"Low stock: {p.get('name') or 'Product'}",
                            body=f"Only {qty} left in stock.",
                            url="/products",
                        )
            except Exception:
                pass
            return sale
        else:
            raise HTTPException(status_code=500, detail="Sale failed")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


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


@router.post("/returns", response_model=ReturnResponse, status_code=201)
def process_return(payload: ReturnCreate, ctx: RequestContext = Depends(get_current_context)):
    supabase = supabase_client.get_supabase_client()

    try:
        product_res = supabase.table("products").select("*").eq("id", payload.product_id).eq("store_id", ctx.store_id).single().execute()
        if not product_res.data:
            raise HTTPException(status_code=404, detail="Product not found")

        product = product_res.data
        refund_amount = product["price"] * payload.quantity_returned

        new_quantity = (product.get("quantity") or 0) + payload.quantity_returned
        supabase.table("products").update({"quantity": new_quantity}).eq("id", payload.product_id).execute()

        return_record = supabase.table("sales").insert(
            {
                "store_id": ctx.store_id,
                "product_id": payload.product_id,
                "quantity_sold": -payload.quantity_returned,
                "total_price": -refund_amount,
                "sold_by": ctx.user_id,
                "timestamp": now_utc_iso(),
            }
        ).execute()

        if not return_record.data:
            raise HTTPException(status_code=500, detail="Failed to record return")

        record = return_record.data[0]

        audit_log.log_audit_event(
            ctx.store_id,
            ctx.user_id,
            "create",
            "return",
            str(record.get("id", "")),
            f"Return: product {payload.product_id}, qty {payload.quantity_returned}, refund R{refund_amount:.2f}",
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
            "store_id": ctx.store_id,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/products/{product_id}/barcode", response_model=BarcodeResponse)
def generate_barcode(
    product_id: int,
    request: BarcodeGenerateRequest = None,
    ctx: RequestContext = Depends(get_current_context),
):
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")

    supabase = supabase_client.get_supabase_client()

    product_res = supabase.table("products").select("*").eq("id", product_id).eq("store_id", ctx.store_id).single().execute()
    if not product_res.data:
        raise HTTPException(status_code=404, detail="Product not found")

    product = product_res.data
    barcode_type = request.barcode_type if request else "CODE128"

    barcode_value = f"{ctx.store_id[:8]}-{product_id:06d}"

    try:
        import barcode
        from barcode.writer import ImageWriter
        import qrcode

        buffer = BytesIO()

        if barcode_type == "QR":
            qr = qrcode.QRCode(version=1, box_size=10, border=4)
            qr.add_data(barcode_value)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            img.save(buffer, format="PNG")
        else:
            barcode_class = barcode.get_barcode_class(barcode_type.lower())
            bc = barcode_class(barcode_value, writer=ImageWriter())
            bc.write(buffer)

        buffer.seek(0)
        barcode_image_b64 = base64.b64encode(buffer.read()).decode("utf-8")

        supabase.table("products").update({"barcode": barcode_value, "barcode_type": barcode_type}).eq("id", product_id).eq(
            "store_id", ctx.store_id
        ).execute()

        return BarcodeResponse(
            product_id=product_id,
            barcode=barcode_value,
            barcode_type=barcode_type,
            barcode_image=f"data:image/png;base64,{barcode_image_b64}",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate barcode: {str(e)}")


@router.get("/barcode/lookup/{barcode}", response_model=BarcodeLookupResponse)
def lookup_barcode(barcode: str, ctx: RequestContext = Depends(get_current_context)):
    supabase = supabase_client.get_supabase_client()

    product_res = supabase.table("products").select("*").eq("barcode", barcode).eq("store_id", ctx.store_id).single().execute()
    if not product_res.data:
        raise HTTPException(status_code=404, detail="Product not found for this barcode")

    p = product_res.data
    return BarcodeLookupResponse(
        product_id=p["id"],
        name=p["name"],
        price=float(p["price"]),
        quantity=int(p["quantity"]),
        barcode=barcode,
    )
