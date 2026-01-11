from typing import Optional, List
from pydantic import BaseModel, Field, conint, confloat
from datetime import datetime


class ProductCreate(BaseModel):
    id: Optional[int] = None  # allow client-specified numeric id (internal)
    sku: Optional[str] = Field(None, max_length=255)  # user-controlled string id
    name: str = Field(..., min_length=1, max_length=255)
    price: confloat(ge=0)  # type: ignore[valid-type]
    quantity: conint(ge=0)  # type: ignore[valid-type]
    cost_price: Optional[confloat(ge=0)] = 0  # type: ignore[valid-type]
    image_url: Optional[str] = Field(None, max_length=2000)  # URL to product image


class ProductUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    price: Optional[confloat(ge=0)]  # type: ignore[valid-type]
    quantity: Optional[conint(ge=0)]  # type: ignore[valid-type]
    cost_price: Optional[confloat(ge=0)]  # type: ignore[valid-type]
    sku: Optional[str] = Field(None, max_length=255)
    image_url: Optional[str] = Field(None, max_length=2000)


class Product(BaseModel):
    id: int
    sku: Optional[str] = None
    name: str
    price: float
    quantity: int
    cost_price: Optional[float] = 0
    image_url: Optional[str] = None


class SaleCreate(BaseModel):
    product_id: int
    quantity_sold: conint(gt=0)  # type: ignore[valid-type]


class Sale(BaseModel):
    id: int
    product_id: int
    quantity_sold: int
    total_price: float
    timestamp: datetime


class ReportTotals(BaseModel):
    total_sales_count: int
    total_revenue: float
    total_profit: float


class ReportResponse(BaseModel):
    totals: ReportTotals
    transactions: List[Sale]


