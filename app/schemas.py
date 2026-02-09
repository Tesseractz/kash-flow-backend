from typing import Optional, List
from pydantic import BaseModel, Field, conint, confloat
from datetime import datetime, date
from uuid import UUID


# ===========================================
# CATEGORY SCHEMAS
# ===========================================
class CategoryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    color: Optional[str] = Field("#6366f1", max_length=7)
    icon: Optional[str] = Field("tag", max_length=50)
    sort_order: Optional[int] = 0
    is_active: Optional[bool] = True


class CategoryUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    color: Optional[str] = Field(None, max_length=7)
    icon: Optional[str] = Field(None, max_length=50)
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


class Category(BaseModel):
    id: str
    store_id: str
    name: str
    description: Optional[str] = None
    color: str = "#6366f1"
    icon: str = "tag"
    sort_order: int = 0
    is_active: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


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
# DISCOUNT SCHEMAS
# ===========================================
class DiscountCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    code: Optional[str] = Field(None, max_length=50)
    discount_type: str = Field(..., pattern="^(percentage|fixed)$")
    discount_value: confloat(gt=0) = None  # type: ignore[valid-type]
    min_purchase_amount: Optional[confloat(ge=0)] = 0  # type: ignore[valid-type]
    max_discount_amount: Optional[confloat(ge=0)] = None  # type: ignore[valid-type]
    usage_limit: Optional[int] = None
    per_customer_limit: Optional[int] = 1
    applies_to: Optional[str] = Field("all", pattern="^(all|category|product)$")
    applies_to_id: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    is_active: Optional[bool] = True


class DiscountUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    code: Optional[str] = Field(None, max_length=50)
    discount_type: Optional[str] = Field(None, pattern="^(percentage|fixed)$")
    discount_value: Optional[confloat(gt=0)] = None  # type: ignore[valid-type]
    min_purchase_amount: Optional[confloat(ge=0)] = None  # type: ignore[valid-type]
    max_discount_amount: Optional[confloat(ge=0)] = None  # type: ignore[valid-type]
    usage_limit: Optional[int] = None
    per_customer_limit: Optional[int] = None
    applies_to: Optional[str] = Field(None, pattern="^(all|category|product)$")
    applies_to_id: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    is_active: Optional[bool] = None


class Discount(BaseModel):
    id: str
    store_id: str
    name: str
    description: Optional[str] = None
    code: Optional[str] = None
    discount_type: str
    discount_value: float
    min_purchase_amount: float = 0
    max_discount_amount: Optional[float] = None
    usage_limit: Optional[int] = None
    usage_count: int = 0
    per_customer_limit: int = 1
    applies_to: str = "all"
    applies_to_id: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    is_active: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ApplyDiscountRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=50)
    cart_total: confloat(ge=0)  # type: ignore[valid-type]
    customer_id: Optional[str] = None


class ApplyDiscountResponse(BaseModel):
    discount_id: str
    discount_name: str
    discount_type: str
    discount_value: float
    discount_amount: float  # Actual amount to deduct
    final_total: float


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
    category: Optional[Category] = None  # Nested category object


class SaleItemCreate(BaseModel):
    product_id: int
    quantity_sold: conint(gt=0)  # type: ignore[valid-type]


class SaleCreate(BaseModel):
    product_id: int
    quantity_sold: conint(gt=0)  # type: ignore[valid-type]
    customer_id: Optional[str] = None  # UUID of customer
    discount_code: Optional[str] = None  # Coupon code to apply


class BatchSaleCreate(BaseModel):
    items: List[SaleItemCreate]
    customer_id: Optional[str] = None
    discount_code: Optional[str] = None


class Sale(BaseModel):
    id: int
    product_id: int
    quantity_sold: int
    total_price: float
    subtotal: Optional[float] = None  # Price before discount
    discount_amount: Optional[float] = 0  # Discount applied
    discount_id: Optional[str] = None
    customer_id: Optional[str] = None
    timestamp: datetime
    profit: Optional[float] = None  # Profit for this sale (revenue - cost)
    customer: Optional[Customer] = None  # Nested customer object
    discount: Optional[Discount] = None  # Nested discount object


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
# EMPLOYEE SHIFT SCHEMAS
# ===========================================
class ShiftCreate(BaseModel):
    user_id: str
    shift_date: date
    scheduled_start: Optional[str] = None  # Time string HH:MM
    scheduled_end: Optional[str] = None
    notes: Optional[str] = None


class ShiftUpdate(BaseModel):
    scheduled_start: Optional[str] = None
    scheduled_end: Optional[str] = None
    actual_start: Optional[datetime] = None
    actual_end: Optional[datetime] = None
    break_minutes: Optional[int] = None
    status: Optional[str] = None
    notes: Optional[str] = None


class Shift(BaseModel):
    id: str
    store_id: str
    user_id: str
    shift_date: date
    scheduled_start: Optional[str] = None
    scheduled_end: Optional[str] = None
    actual_start: Optional[datetime] = None
    actual_end: Optional[datetime] = None
    break_minutes: int = 0
    status: str = "scheduled"
    notes: Optional[str] = None
    created_at: Optional[datetime] = None
    user_name: Optional[str] = None  # Joined from profiles


# ===========================================
# TIME CLOCK SCHEMAS
# ===========================================
class ClockInRequest(BaseModel):
    shift_id: Optional[str] = None
    notes: Optional[str] = None


class ClockOutRequest(BaseModel):
    notes: Optional[str] = None


class TimeClockEntry(BaseModel):
    id: str
    store_id: str
    user_id: str
    shift_id: Optional[str] = None
    clock_in: datetime
    clock_out: Optional[datetime] = None
    break_start: Optional[datetime] = None
    break_end: Optional[datetime] = None
    total_hours: Optional[float] = None
    overtime_hours: float = 0
    notes: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    user_name: Optional[str] = None


# ===========================================
# COMMISSION SCHEMAS
# ===========================================
class Commission(BaseModel):
    id: str
    store_id: str
    user_id: str
    sale_id: Optional[int] = None
    commission_rate: float
    sale_amount: float
    commission_amount: float
    status: str = "pending"
    paid_at: Optional[datetime] = None
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    created_at: Optional[datetime] = None
    user_name: Optional[str] = None


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


class EmployeeSalesReport(BaseModel):
    user_id: str
    user_name: str
    total_sales: int
    total_revenue: float
    total_profit: float
    commission_earned: float
    avg_transaction_value: float
    hours_worked: float


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

