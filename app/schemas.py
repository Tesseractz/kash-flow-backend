from typing import Optional, List
from pydantic import BaseModel, Field, conint, confloat
from datetime import datetime, date
from uuid import UUID


# ===========================================
# CUSTOMER SCHEMAS
# ===========================================
class CustomerCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    email: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=50)
    address: Optional[str] = None
    notes: Optional[str] = None
    birthday: Optional[date] = None


class CustomerUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    email: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=50)
    address: Optional[str] = None
    notes: Optional[str] = None
    birthday: Optional[date] = None
    loyalty_points: Optional[int] = None
    is_active: Optional[bool] = None


class Customer(BaseModel):
    id: str
    store_id: str
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None
    loyalty_points: int = 0
    total_spent: float = 0
    total_visits: int = 0
    last_visit_at: Optional[datetime] = None
    birthday: Optional[date] = None
    is_active: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ===========================================
# PRODUCT SCHEMAS (updated)
# ===========================================
class ProductCreate(BaseModel):
    id: Optional[int] = None  # allow client-specified numeric id (internal)
    sku: Optional[str] = Field(None, max_length=255)  # user-controlled string id
    name: str = Field(..., min_length=1, max_length=255)
    price: confloat(ge=0)  # type: ignore[valid-type]
    quantity: conint(ge=0)  # type: ignore[valid-type]
    cost_price: Optional[confloat(ge=0)] = 0  # type: ignore[valid-type]
    image_url: Optional[str] = Field(None, max_length=2000)  # URL to product image
    category_id: Optional[str] = None  # UUID of category


class ProductUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    price: Optional[confloat(ge=0)] = None  # type: ignore[valid-type]
    quantity: Optional[conint(ge=0)] = None  # type: ignore[valid-type]
    cost_price: Optional[confloat(ge=0)] = None  # type: ignore[valid-type]
    sku: Optional[str] = Field(None, max_length=255)
    image_url: Optional[str] = Field(None, max_length=2000)
    category_id: Optional[str] = None  # UUID of category


class Product(BaseModel):
    id: int
    sku: Optional[str] = None
    name: str
    price: float
    quantity: int
    cost_price: Optional[float] = 0
    image_url: Optional[str] = None
    category_id: Optional[str] = None


class SaleItemCreate(BaseModel):
    product_id: int
    quantity_sold: conint(gt=0)  # type: ignore[valid-type]


class SaleCreate(BaseModel):
    product_id: int
    quantity_sold: conint(gt=0)  # type: ignore[valid-type]
    customer_id: Optional[str] = None  # UUID of customer


class BatchSaleCreate(BaseModel):
    items: List[SaleItemCreate]
    customer_id: Optional[str] = None


class Sale(BaseModel):
    id: int
    product_id: int
    quantity_sold: int
    total_price: float
    subtotal: Optional[float] = None
    customer_id: Optional[str] = None
    timestamp: datetime
    profit: Optional[float] = None  # Profit for this sale (revenue - cost)
    customer: Optional[Customer] = None  # Nested customer object


class ReportTotals(BaseModel):
    total_sales_count: int
    total_revenue: float
    total_profit: float


class ReportResponse(BaseModel):
    totals: ReportTotals
    transactions: List[Sale]


# ===========================================
# EXPENSE SCHEMAS
# ===========================================
class ExpenseCreate(BaseModel):
    category: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    amount: confloat(gt=0)  # type: ignore[valid-type]
    expense_date: date
    payment_method: Optional[str] = Field("cash", max_length=50)
    vendor: Optional[str] = Field(None, max_length=255)
    receipt_url: Optional[str] = None
    notes: Optional[str] = None
    is_recurring: Optional[bool] = False
    recurring_frequency: Optional[str] = None
    tags: Optional[List[str]] = None


class ExpenseUpdate(BaseModel):
    category: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    amount: Optional[confloat(gt=0)] = None  # type: ignore[valid-type]
    expense_date: Optional[date] = None
    payment_method: Optional[str] = Field(None, max_length=50)
    vendor: Optional[str] = Field(None, max_length=255)
    receipt_url: Optional[str] = None
    notes: Optional[str] = None
    is_recurring: Optional[bool] = None
    recurring_frequency: Optional[str] = None
    tags: Optional[List[str]] = None


class Expense(BaseModel):
    id: str
    store_id: str
    user_id: Optional[str] = None
    category: str
    description: Optional[str] = None
    amount: float
    expense_date: date
    payment_method: str = "cash"
    vendor: Optional[str] = None
    receipt_url: Optional[str] = None
    notes: Optional[str] = None
    is_recurring: bool = False
    recurring_frequency: Optional[str] = None
    tags: Optional[List[str]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ExpenseCategory(BaseModel):
    id: str
    store_id: str
    name: str
    icon: str = "receipt"
    color: str = "#6b7280"
    is_system: bool = False


# ===========================================
# ENHANCED REPORTING SCHEMAS
# ===========================================
class ProfitLossReport(BaseModel):
    period_start: date
    period_end: date
    total_revenue: float
    total_cost_of_goods: float
    gross_profit: float
    total_expenses: float
    net_profit: float
    expense_breakdown: List[dict]  # {category, amount}
    revenue_by_day: List[dict]  # {date, revenue, profit}


class TaxReport(BaseModel):
    period_start: date
    period_end: date
    total_sales: float
    tax_collected: float
    tax_rate: float
    taxable_sales: float
    transactions_count: int


class InventoryValuationReport(BaseModel):
    total_products: int
    total_quantity: int
    total_cost_value: float
    total_retail_value: float
    potential_profit: float
    low_stock_count: int
    out_of_stock_count: int
    categories: List[dict]  # {category, quantity, cost_value, retail_value}


# ===========================================
# BARCODE SCHEMAS
# ===========================================
class BarcodeGenerateRequest(BaseModel):
    product_id: int
    barcode_type: str = "CODE128"  # CODE128, EAN13, QR


class BarcodeResponse(BaseModel):
    product_id: int
    barcode: str
    barcode_type: str
    barcode_image: str  # Base64 encoded image


class BarcodeLookupResponse(BaseModel):
    product_id: int
    name: str
    price: float
    quantity: int
    barcode: str


# ===========================================
# PRIVACY & COMPLIANCE SCHEMAS
# ===========================================
class ConsentType(str):
    """Valid consent types."""
    TERMS = "terms"
    PRIVACY = "privacy"
    MARKETING = "marketing"
    COOKIES = "cookies"
    NOTIFICATIONS = "notifications"


class ConsentRecord(BaseModel):
    consent_type: str
    consented: bool
    consent_version: Optional[str] = None
    consented_at: Optional[datetime] = None


class ConsentUpdate(BaseModel):
    consent_type: str
    consented: bool
    consent_version: Optional[str] = None


class UserConsent(BaseModel):
    id: str
    user_id: str
    consent_type: str
    consented: bool
    consent_version: Optional[str] = None
    consented_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None


class PrivacySettings(BaseModel):
    marketing_emails_enabled: bool = False
    push_notifications_enabled: bool = False
    data_analytics_enabled: bool = True
    two_factor_enabled: bool = False


class PrivacySettingsUpdate(BaseModel):
    marketing_emails_enabled: Optional[bool] = None
    push_notifications_enabled: Optional[bool] = None
    data_analytics_enabled: Optional[bool] = None


class UserSession(BaseModel):
    id: str
    device_info: Optional[dict] = None
    last_active_at: Optional[datetime] = None
    is_current: bool = False
    created_at: Optional[datetime] = None


class DataExportRequest(BaseModel):
    id: str
    status: str
    requested_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    download_url: Optional[str] = None
    expires_at: Optional[datetime] = None


class AccountDeletionRequest(BaseModel):
    id: str
    status: str
    reason: Optional[str] = None
    requested_at: Optional[datetime] = None
    scheduled_deletion_at: Optional[datetime] = None


class AccountDeletionCreate(BaseModel):
    reason: Optional[str] = None
    confirm_password: str  # Require password confirmation for deletion


class CookiePreferences(BaseModel):
    essential: bool = True  # Always true
    analytics: bool = False
    marketing: bool = False
    functional: bool = True


class TwoFactorSetupResponse(BaseModel):
    secret: str
    qr_code_url: str
    backup_codes: List[str]


class TwoFactorVerifyRequest(BaseModel):
    code: str


class SignupConsents(BaseModel):
    """Consents required during signup."""
    terms_accepted: bool
    privacy_accepted: bool
    marketing_opted_in: bool = False

