"""
Tests for Expenses, Employee Management, and Barcode features.
"""
import pytest
from datetime import date, datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from uuid import uuid4

from app.main import app
from app.deps import get_current_context, RequestContext


# Test fixtures
@pytest.fixture
def admin_context():
    """Returns a mock admin context."""
    return RequestContext(
        user_id=str(uuid4()),
        store_id=str(uuid4()),
        role="admin"
    )


@pytest.fixture
def cashier_context():
    """Returns a mock cashier context."""
    return RequestContext(
        user_id=str(uuid4()),
        store_id=str(uuid4()),
        role="cashier"
    )


@pytest.fixture
def client():
    """Returns a test client."""
    return TestClient(app)


# ============================================
# EXPENSE TESTS
# ============================================
class TestExpensesAPI:
    
    def test_list_expenses_admin_only(self, client, cashier_context):
        """Cashiers cannot list expenses."""
        app.dependency_overrides[get_current_context] = lambda: cashier_context
        try:
            response = client.get("/expenses")
            assert response.status_code == 403
            assert "Admins only" in response.json()["detail"]
        finally:
            app.dependency_overrides.clear()
    
    @patch("app.main.get_supabase_client")
    def test_list_expenses_success(self, mock_supabase, client, admin_context):
        """Admin can list expenses."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        
        expenses_data = [
            {
                "id": str(uuid4()),
                "store_id": admin_context.store_id,
                "category": "Rent",
                "amount": 5000.00,
                "expense_date": "2026-02-01",
                "payment_method": "bank_transfer"
            }
        ]
        mock_client.table().select().eq().gte().lte().eq().order().execute.return_value = MagicMock(data=expenses_data)
        mock_client.table().select().eq().order().execute.return_value = MagicMock(data=expenses_data)
        
        app.dependency_overrides[get_current_context] = lambda: admin_context
        try:
            response = client.get("/expenses")
            assert response.status_code == 200
        finally:
            app.dependency_overrides.clear()
    
    @patch("app.main.get_supabase_client")
    @patch("app.main.log_audit_event")
    def test_create_expense_success(self, mock_audit, mock_supabase, client, admin_context):
        """Admin can create an expense."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        
        expense_id = str(uuid4())
        mock_client.table().insert().execute.return_value = MagicMock(data=[{
            "id": expense_id,
            "store_id": admin_context.store_id,
            "user_id": admin_context.user_id,
            "category": "Utilities",
            "amount": 500.00,
            "expense_date": "2026-02-09",
            "payment_method": "cash",
            "description": "Electricity bill",
            "vendor": None,
            "notes": None,
            "is_recurring": False,
            "recurring_frequency": None,
            "tags": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }])
        
        app.dependency_overrides[get_current_context] = lambda: admin_context
        try:
            response = client.post("/expenses", json={
                "category": "Utilities",
                "amount": 500.00,
                "expense_date": "2026-02-09",
                "description": "Electricity bill"
            })
            assert response.status_code == 201
            assert response.json()["category"] == "Utilities"
            mock_audit.assert_called_once()
        finally:
            app.dependency_overrides.clear()
    
    def test_create_expense_cashier_forbidden(self, client, cashier_context):
        """Cashiers cannot create expenses."""
        app.dependency_overrides[get_current_context] = lambda: cashier_context
        try:
            response = client.post("/expenses", json={
                "category": "Utilities",
                "amount": 500.00,
                "expense_date": "2026-02-09"
            })
            assert response.status_code == 403
        finally:
            app.dependency_overrides.clear()
    
    @patch("app.main.get_supabase_client")
    @patch("app.main.log_audit_event")
    def test_update_expense_success(self, mock_audit, mock_supabase, client, admin_context):
        """Admin can update an expense."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        
        expense_id = str(uuid4())
        mock_client.table().update().eq().eq().execute.return_value = MagicMock(data=[{
            "id": expense_id,
            "store_id": admin_context.store_id,
            "category": "Rent",
            "amount": 5500.00,
            "expense_date": "2026-02-01",
            "payment_method": "bank_transfer"
        }])
        
        app.dependency_overrides[get_current_context] = lambda: admin_context
        try:
            response = client.put(f"/expenses/{expense_id}", json={
                "amount": 5500.00
            })
            assert response.status_code == 200
            assert response.json()["amount"] == 5500.00
        finally:
            app.dependency_overrides.clear()
    
    @patch("app.main.get_supabase_client")
    @patch("app.main.log_audit_event")
    def test_delete_expense_success(self, mock_audit, mock_supabase, client, admin_context):
        """Admin can delete an expense."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        mock_client.table().delete().eq().eq().execute.return_value = MagicMock()
        
        expense_id = str(uuid4())
        
        app.dependency_overrides[get_current_context] = lambda: admin_context
        try:
            response = client.delete(f"/expenses/{expense_id}")
            assert response.status_code == 204
        finally:
            app.dependency_overrides.clear()


# ============================================
# SHIFT TESTS
# ============================================
class TestShiftsAPI:
    
    @patch("app.main.get_supabase_client")
    def test_list_shifts_admin_sees_all(self, mock_supabase, client, admin_context):
        """Admin can see all shifts."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        
        shifts_data = [
            {
                "id": str(uuid4()), 
                "store_id": admin_context.store_id,
                "user_id": "user1", 
                "shift_date": "2026-02-09", 
                "scheduled_start": "09:00",
                "scheduled_end": "17:00",
                "actual_start": None,
                "actual_end": None,
                "break_minutes": 0,
                "status": "scheduled", 
                "notes": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "profiles": {"name": "John"}
            }
        ]
        mock_client.table().select().eq().gte().lte().eq().eq().eq().order().execute.return_value = MagicMock(data=shifts_data)
        mock_client.table().select().eq().order().execute.return_value = MagicMock(data=shifts_data)
        
        app.dependency_overrides[get_current_context] = lambda: admin_context
        try:
            response = client.get("/shifts")
            assert response.status_code == 200
        finally:
            app.dependency_overrides.clear()
    
    @patch("app.main.get_supabase_client")
    def test_list_shifts_cashier_sees_own(self, mock_supabase, client, cashier_context):
        """Cashiers only see their own shifts."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        
        # The query should have .eq("user_id", ctx.user_id) added for non-admins
        mock_client.table().select().eq().eq().order().execute.return_value = MagicMock(data=[])
        
        app.dependency_overrides[get_current_context] = lambda: cashier_context
        try:
            response = client.get("/shifts")
            assert response.status_code == 200
        finally:
            app.dependency_overrides.clear()
    
    @patch("app.main.get_supabase_client")
    @patch("app.main.log_audit_event")
    def test_create_shift_admin_only(self, mock_audit, mock_supabase, client, admin_context):
        """Only admin can create shifts."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        
        shift_id = str(uuid4())
        mock_client.table().insert().execute.return_value = MagicMock(data=[{
            "id": shift_id,
            "store_id": admin_context.store_id,
            "user_id": str(uuid4()),
            "shift_date": "2026-02-10",
            "scheduled_start": "09:00",
            "scheduled_end": "17:00",
            "status": "scheduled",
            "break_minutes": 0
        }])
        
        app.dependency_overrides[get_current_context] = lambda: admin_context
        try:
            response = client.post("/shifts", json={
                "user_id": str(uuid4()),
                "shift_date": "2026-02-10",
                "scheduled_start": "09:00",
                "scheduled_end": "17:00"
            })
            assert response.status_code == 201
        finally:
            app.dependency_overrides.clear()
    
    def test_create_shift_cashier_forbidden(self, client, cashier_context):
        """Cashiers cannot create shifts."""
        app.dependency_overrides[get_current_context] = lambda: cashier_context
        try:
            response = client.post("/shifts", json={
                "user_id": str(uuid4()),
                "shift_date": "2026-02-10"
            })
            assert response.status_code == 403
        finally:
            app.dependency_overrides.clear()


# ============================================
# TIME CLOCK TESTS
# ============================================
class TestTimeClockAPI:
    
    @patch("app.main.get_supabase_client")
    @patch("app.main.log_audit_event")
    def test_clock_in_success(self, mock_audit, mock_supabase, client, cashier_context):
        """User can clock in."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        
        # No existing clock-in
        mock_client.table().select().eq().eq().is_().execute.return_value = MagicMock(data=[])
        
        entry_id = str(uuid4())
        mock_client.table().insert().execute.return_value = MagicMock(data=[{
            "id": entry_id,
            "store_id": cashier_context.store_id,
            "user_id": cashier_context.user_id,
            "clock_in": datetime.now(timezone.utc).isoformat(),
            "clock_out": None,
            "total_hours": None,
            "overtime_hours": 0
        }])
        
        app.dependency_overrides[get_current_context] = lambda: cashier_context
        try:
            response = client.post("/time-clock/clock-in", json={})
            assert response.status_code == 201
            mock_audit.assert_called_once()
        finally:
            app.dependency_overrides.clear()
    
    @patch("app.main.get_supabase_client")
    def test_clock_in_already_clocked_in(self, mock_supabase, client, cashier_context):
        """Cannot clock in if already clocked in."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        
        # Already clocked in
        mock_client.table().select().eq().eq().is_().execute.return_value = MagicMock(data=[{"id": str(uuid4())}])
        
        app.dependency_overrides[get_current_context] = lambda: cashier_context
        try:
            response = client.post("/time-clock/clock-in", json={})
            assert response.status_code == 400
            assert "Already clocked in" in response.json()["detail"]
        finally:
            app.dependency_overrides.clear()
    
    @patch("app.main.get_supabase_client")
    @patch("app.main.log_audit_event")
    def test_clock_out_success(self, mock_audit, mock_supabase, client, cashier_context):
        """User can clock out."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        
        entry_id = str(uuid4())
        clock_in_time = (datetime.now(timezone.utc) - timedelta(hours=8)).isoformat()
        
        # Find active clock-in
        mock_client.table().select().eq().eq().is_().single().execute.return_value = MagicMock(data={
            "id": entry_id,
            "clock_in": clock_in_time,
            "break_minutes": 30,
            "notes": ""
        })
        
        # Update with clock-out
        mock_client.table().update().eq().execute.return_value = MagicMock(data=[{
            "id": entry_id,
            "store_id": cashier_context.store_id,
            "user_id": cashier_context.user_id,
            "clock_in": clock_in_time,
            "clock_out": datetime.now(timezone.utc).isoformat(),
            "total_hours": 7.5,
            "overtime_hours": 0
        }])
        
        app.dependency_overrides[get_current_context] = lambda: cashier_context
        try:
            response = client.post("/time-clock/clock-out", json={})
            assert response.status_code == 200
        finally:
            app.dependency_overrides.clear()
    
    @patch("app.main.get_supabase_client")
    def test_clock_out_not_clocked_in(self, mock_supabase, client, cashier_context):
        """Cannot clock out if not clocked in."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        
        # Not clocked in
        mock_client.table().select().eq().eq().is_().single().execute.return_value = MagicMock(data=None)
        
        app.dependency_overrides[get_current_context] = lambda: cashier_context
        try:
            response = client.post("/time-clock/clock-out", json={})
            assert response.status_code == 400
            assert "Not clocked in" in response.json()["detail"]
        finally:
            app.dependency_overrides.clear()
    
    @patch("app.main.get_supabase_client")
    def test_get_current_clock_status(self, mock_supabase, client, cashier_context):
        """Get current clock status."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        
        mock_client.table().select().eq().eq().is_().execute.return_value = MagicMock(data=[])
        
        app.dependency_overrides[get_current_context] = lambda: cashier_context
        try:
            response = client.get("/time-clock/current")
            assert response.status_code == 200
            assert response.json()["clocked_in"] is False
        finally:
            app.dependency_overrides.clear()


# ============================================
# COMMISSION TESTS
# ============================================
class TestCommissionsAPI:
    
    @patch("app.main.get_supabase_client")
    @patch("app.main.log_audit_event")
    def test_approve_commission_admin_only(self, mock_audit, mock_supabase, client, admin_context):
        """Only admin can approve commissions."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        
        commission_id = str(uuid4())
        mock_client.table().update().eq().eq().execute.return_value = MagicMock(data=[{
            "id": commission_id,
            "store_id": admin_context.store_id,
            "user_id": str(uuid4()),
            "commission_rate": 5.0,
            "sale_amount": 1000.0,
            "commission_amount": 50.0,
            "status": "approved"
        }])
        
        app.dependency_overrides[get_current_context] = lambda: admin_context
        try:
            response = client.post(f"/commissions/{commission_id}/approve")
            assert response.status_code == 200
            assert response.json()["status"] == "approved"
        finally:
            app.dependency_overrides.clear()
    
    def test_approve_commission_cashier_forbidden(self, client, cashier_context):
        """Cashiers cannot approve commissions."""
        commission_id = str(uuid4())
        
        app.dependency_overrides[get_current_context] = lambda: cashier_context
        try:
            response = client.post(f"/commissions/{commission_id}/approve")
            assert response.status_code == 403
        finally:
            app.dependency_overrides.clear()
    
    @patch("app.main.get_supabase_client")
    @patch("app.main.log_audit_event")
    def test_pay_commission(self, mock_audit, mock_supabase, client, admin_context):
        """Admin can mark commission as paid."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        
        commission_id = str(uuid4())
        user_id = str(uuid4())
        mock_client.table().update().eq().eq().execute.return_value = MagicMock(data=[{
            "id": commission_id,
            "store_id": admin_context.store_id,
            "user_id": user_id,
            "sale_id": 1,
            "commission_rate": 5.0,
            "sale_amount": 1000.0,
            "commission_amount": 50.0,
            "status": "paid",
            "paid_at": datetime.now(timezone.utc).isoformat(),
            "period_start": None,
            "period_end": None,
            "created_at": datetime.now(timezone.utc).isoformat()
        }])
        
        app.dependency_overrides[get_current_context] = lambda: admin_context
        try:
            response = client.post(f"/commissions/{commission_id}/pay")
            assert response.status_code == 200
            assert response.json()["status"] == "paid"
        finally:
            app.dependency_overrides.clear()


# ============================================
# BARCODE TESTS
# ============================================
class TestBarcodeAPI:
    
    def test_generate_barcode_cashier_forbidden(self, client, cashier_context):
        """Cashiers cannot generate barcodes."""
        app.dependency_overrides[get_current_context] = lambda: cashier_context
        try:
            response = client.post("/products/1/barcode", json={"product_id": 1})
            assert response.status_code == 403
        finally:
            app.dependency_overrides.clear()
    
    @patch("app.main.get_supabase_client")
    def test_generate_barcode_product_not_found(self, mock_supabase, client, admin_context):
        """Returns 404 if product not found."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        
        mock_client.table().select().eq().eq().single().execute.return_value = MagicMock(data=None)
        
        app.dependency_overrides[get_current_context] = lambda: admin_context
        try:
            response = client.post("/products/999/barcode", json={"product_id": 999})
            assert response.status_code == 404
        finally:
            app.dependency_overrides.clear()
    
    @patch("app.main.get_supabase_client")
    def test_lookup_barcode_not_found(self, mock_supabase, client, cashier_context):
        """Returns 404 if barcode not found."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        
        mock_client.table().select().eq().eq().single().execute.return_value = MagicMock(data=None)
        
        app.dependency_overrides[get_current_context] = lambda: cashier_context
        try:
            response = client.get("/barcode/lookup/INVALID123")
            assert response.status_code == 404
        finally:
            app.dependency_overrides.clear()
    
    @patch("app.main.get_supabase_client")
    def test_lookup_barcode_success(self, mock_supabase, client, cashier_context):
        """Successfully looks up a product by barcode."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        
        mock_client.table().select().eq().eq().single().execute.return_value = MagicMock(data={
            "id": 1,
            "name": "Test Product",
            "price": 100.00,
            "quantity": 50,
            "barcode": "TEST-000001"
        })
        
        app.dependency_overrides[get_current_context] = lambda: cashier_context
        try:
            response = client.get("/barcode/lookup/TEST-000001")
            assert response.status_code == 200
            assert response.json()["name"] == "Test Product"
            assert response.json()["price"] == 100.00
        finally:
            app.dependency_overrides.clear()


# ============================================
# ENHANCED REPORTS TESTS
# ============================================
class TestEnhancedReportsAPI:
    
    @patch("app.main.get_supabase_client")
    def test_profit_loss_report(self, mock_supabase, client, admin_context):
        """Generate profit & loss report."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        
        # Sales data
        mock_sales = [
            {"total_price": 1000.00, "profit": 300.00, "timestamp": "2026-02-05T10:00:00Z"},
            {"total_price": 500.00, "profit": 150.00, "timestamp": "2026-02-06T14:00:00Z"},
        ]
        
        # Expenses data
        mock_expenses = [
            {"category": "Rent", "amount": 200.00, "expense_date": "2026-02-05"},
            {"category": "Utilities", "amount": 50.00, "expense_date": "2026-02-06"},
        ]
        
        # Configure mocks
        mock_client.table().select().eq().gte().lte().execute.side_effect = [
            MagicMock(data=mock_sales),  # First call for sales
            MagicMock(data=mock_expenses),  # Second call for expenses
        ]
        
        app.dependency_overrides[get_current_context] = lambda: admin_context
        try:
            response = client.get("/reports/profit-loss", params={
                "start_date": "2026-02-01",
                "end_date": "2026-02-28"
            })
            assert response.status_code == 200
            data = response.json()
            assert data["total_revenue"] == 1500.00
            assert data["gross_profit"] == 450.00
            assert data["total_expenses"] == 250.00
            assert data["net_profit"] == 200.00
        finally:
            app.dependency_overrides.clear()
    
    def test_profit_loss_cashier_forbidden(self, client, cashier_context):
        """Cashiers cannot access P&L report."""
        app.dependency_overrides[get_current_context] = lambda: cashier_context
        try:
            response = client.get("/reports/profit-loss", params={
                "start_date": "2026-02-01",
                "end_date": "2026-02-28"
            })
            assert response.status_code == 403
        finally:
            app.dependency_overrides.clear()
    
    @patch("app.main.get_supabase_client")
    def test_tax_report(self, mock_supabase, client, admin_context):
        """Generate tax report."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        
        mock_sales = [
            {"total_price": 115.00},  # VAT inclusive
            {"total_price": 230.00},
        ]
        
        mock_client.table().select().eq().gte().lte().execute.return_value = MagicMock(data=mock_sales)
        
        app.dependency_overrides[get_current_context] = lambda: admin_context
        try:
            response = client.get("/reports/tax", params={
                "start_date": "2026-02-01",
                "end_date": "2026-02-28",
                "tax_rate": 15.0
            })
            assert response.status_code == 200
            data = response.json()
            assert data["total_sales"] == 345.00
            assert data["tax_rate"] == 15.0
            assert data["transactions_count"] == 2
            # Taxable sales = 345 / 1.15 = 300
            assert abs(data["taxable_sales"] - 300.00) < 0.01
            # Tax collected = 345 - 300 = 45
            assert abs(data["tax_collected"] - 45.00) < 0.01
        finally:
            app.dependency_overrides.clear()
    
    @patch("app.main.get_supabase_client")
    def test_inventory_valuation_report(self, mock_supabase, client, admin_context):
        """Generate inventory valuation report."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        
        mock_products = [
            {"id": 1, "name": "A", "quantity": 10, "price": 100, "cost_price": 50, "categories": {"name": "Electronics"}},
            {"id": 2, "name": "B", "quantity": 5, "price": 200, "cost_price": 100, "categories": {"name": "Electronics"}},
            {"id": 3, "name": "C", "quantity": 0, "price": 50, "cost_price": 25, "categories": None},  # Out of stock
        ]
        
        mock_client.table().select().eq().execute.return_value = MagicMock(data=mock_products)
        
        app.dependency_overrides[get_current_context] = lambda: admin_context
        try:
            response = client.get("/reports/inventory-valuation", params={"low_stock_threshold": 10})
            assert response.status_code == 200
            data = response.json()
            assert data["total_products"] == 3
            assert data["total_quantity"] == 15
            assert data["out_of_stock_count"] == 1
            assert data["low_stock_count"] == 2  # Products A (10) and B (5) both <= threshold (10)
            # Cost: 10*50 + 5*100 + 0*25 = 500 + 500 = 1000
            assert data["total_cost_value"] == 1000.00
            # Retail: 10*100 + 5*200 + 0*50 = 1000 + 1000 = 2000
            assert data["total_retail_value"] == 2000.00
        finally:
            app.dependency_overrides.clear()
    
    @patch("app.main.get_supabase_client")
    def test_employee_sales_report(self, mock_supabase, client, admin_context):
        """Generate employee sales performance report."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        
        user_id = str(uuid4())
        
        mock_sales = [
            {"sold_by": user_id, "total_price": 500, "profit": 100},
            {"sold_by": user_id, "total_price": 300, "profit": 75},
        ]
        mock_time = [
            {"user_id": user_id, "total_hours": 8.0},
        ]
        mock_commissions = [
            {"user_id": user_id, "commission_amount": 40.0},
        ]
        mock_users = [
            {"id": user_id, "name": "John Doe"},
        ]
        
        mock_client.table().select().eq().gte().lte().execute.side_effect = [
            MagicMock(data=mock_sales),
            MagicMock(data=mock_time),
            MagicMock(data=mock_commissions),
        ]
        mock_client.table().select().eq().execute.return_value = MagicMock(data=mock_users)
        
        app.dependency_overrides[get_current_context] = lambda: admin_context
        try:
            response = client.get("/reports/employee-sales", params={
                "start_date": "2026-02-01",
                "end_date": "2026-02-28"
            })
            assert response.status_code == 200
            data = response.json()
            assert len(data) >= 1
            employee = data[0]
            assert employee["total_revenue"] == 800.0
            assert employee["total_profit"] == 175.0
        finally:
            app.dependency_overrides.clear()


# ============================================
# EXPENSE CATEGORY TESTS
# ============================================
class TestExpenseCategoriesAPI:
    
    @patch("app.main.get_supabase_client")
    def test_list_expense_categories_creates_defaults(self, mock_supabase, client, cashier_context):
        """Creates default categories if none exist."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        
        # First call returns empty, second call returns defaults
        mock_client.table().select().eq().order().execute.side_effect = [
            MagicMock(data=[]),  # No categories initially
            MagicMock(data=[{
                "id": str(uuid4()), 
                "store_id": cashier_context.store_id,
                "name": "Rent", 
                "icon": "home", 
                "color": "#ef4444", 
                "is_system": True
            }]),
        ]
        mock_client.table().insert().execute.return_value = MagicMock()
        
        app.dependency_overrides[get_current_context] = lambda: cashier_context
        try:
            response = client.get("/expense-categories")
            assert response.status_code == 200
            # Should have inserted defaults
            mock_client.table().insert.assert_called()
        finally:
            app.dependency_overrides.clear()
