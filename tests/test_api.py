"""
Integration tests for KashPoint API endpoints.
Uses FastAPI TestClient with mocked Supabase.
"""
import os
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
import json

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set environment variables before importing app
os.environ["SUPABASE_URL"] = "https://test.supabase.co"
os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "test-service-role-key"
os.environ["SUPABASE_JWT_SECRET"] = "test-jwt-secret-key-for-testing-only-must-be-32chars"
os.environ["PASSWORD_ENCRYPTION_KEY"] = "dGVzdC1lbmNyeXB0aW9uLWtleS0xMjM0NTY3ODkw"
# Clear DEV_PLAN_OVERRIDE for tests
os.environ.pop("DEV_PLAN_OVERRIDE", None)


def create_mock_context(user_id="test-user-id", store_id="test-store-id", role="admin"):
    """Create a mock RequestContext."""
    from app.deps import RequestContext
    return RequestContext(user_id=user_id, store_id=store_id, role=role)


@pytest.fixture
def test_client():
    """Create test client with dependency overrides."""
    from app.main import app
    from app.deps import get_current_context
    
    # Override the dependency
    app.dependency_overrides[get_current_context] = lambda: create_mock_context()
    
    client = TestClient(app)
    yield client
    
    # Clean up
    app.dependency_overrides.clear()


@pytest.fixture
def test_client_cashier():
    """Create test client with cashier role."""
    from app.main import app
    from app.deps import get_current_context
    
    app.dependency_overrides[get_current_context] = lambda: create_mock_context(role="cashier")
    
    client = TestClient(app)
    yield client
    
    app.dependency_overrides.clear()


class TestHealthEndpoint:
    """Tests for health check endpoint."""
    
    def test_health_returns_ok(self):
        """Test that health endpoint returns ok status."""
        from app.main import app
        
        client = TestClient(app)
        response = client.get("/health")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "time" in data


class TestProductsAPI:
    """Tests for products API endpoints."""
    
    @patch('app.main.get_supabase_client')
    def test_list_products_returns_empty_list(self, mock_supabase, test_client):
        """Test listing products when there are none."""
        mock_query = MagicMock()
        mock_query.execute.return_value.data = []
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.order.return_value = mock_query
        mock_query.or_.return_value = mock_query
        mock_supabase.return_value.table.return_value = mock_query
        
        response = test_client.get("/products")
        
        assert response.status_code == 200
        assert response.json() == []
    
    @patch('app.main.get_supabase_client')
    def test_list_products_returns_products(self, mock_supabase, test_client):
        """Test listing products returns product data."""
        products = [
            {"id": 1, "name": "Product 1", "price": 100.0, "quantity": 10, "sku": "P1"},
            {"id": 2, "name": "Product 2", "price": 200.0, "quantity": 20, "sku": "P2"},
        ]
        
        mock_query = MagicMock()
        mock_query.execute.return_value.data = products
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.order.return_value = mock_query
        mock_query.or_.return_value = mock_query
        mock_supabase.return_value.table.return_value = mock_query
        
        response = test_client.get("/products")
        
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["name"] == "Product 1"
    
    @patch('app.main.log_audit_event')
    @patch('app.main.enforce_limits_on_create_product')
    @patch('app.main.get_supabase_client')
    def test_create_product_success(self, mock_supabase, mock_limits, mock_audit, test_client):
        """Test creating a product successfully."""
        mock_limits.return_value = None
        mock_audit.return_value = None
        
        new_product = {
            "id": 1,
            "name": "New Product",
            "price": 150.0,
            "quantity": 25,
            "sku": "NP1",
            "cost_price": 80.0,
            "store_id": "test-store-id"
        }
        
        mock_query = MagicMock()
        mock_query.execute.return_value.data = [new_product]
        mock_query.insert.return_value = mock_query
        mock_supabase.return_value.table.return_value = mock_query
        
        response = test_client.post(
            "/products",
            json={"name": "New Product", "price": 150.0, "quantity": 25, "sku": "NP1", "cost_price": 80.0}
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "New Product"
        assert data["price"] == 150.0
    
    def test_create_product_forbidden_for_cashier(self, test_client_cashier):
        """Test that cashiers cannot create products."""
        response = test_client_cashier.post(
            "/products",
            json={"name": "New Product", "price": 150.0, "quantity": 25}
        )
        
        assert response.status_code == 403
        assert "Admins only" in response.json()["detail"]
    
    @patch('app.main.log_audit_event')
    @patch('app.main.get_supabase_client')
    def test_update_product_success(self, mock_supabase, mock_audit):
        """Test updating a product successfully."""
        from app.main import app
        from app.deps import get_current_context
        
        # Set up dependency override
        app.dependency_overrides[get_current_context] = lambda: create_mock_context(role="admin")
        
        mock_audit.return_value = None
        
        updated_product = {
            "id": 1,
            "name": "Updated Product",
            "price": 175.0,
            "quantity": 30,
            "sku": "UP1",
            "cost_price": 90.0
        }
        
        mock_query = MagicMock()
        mock_query.execute.return_value.data = [updated_product]
        mock_query.update.return_value = mock_query
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.single.return_value = mock_query
        mock_supabase.return_value.table.return_value = mock_query
        
        client = TestClient(app)
        
        # Use valid update payload with at least one field
        response = client.put(
            "/products/1",
            json={"name": "Updated Product"}
        )
        
        # Clean up
        app.dependency_overrides.clear()
        
        assert response.status_code == 200, f"Response: {response.json()}"
        data = response.json()
        assert data["name"] == "Updated Product"
    
    @patch('app.main.log_audit_event')
    @patch('app.main.get_supabase_client')
    def test_delete_product_success(self, mock_supabase, mock_audit, test_client):
        """Test deleting a product successfully."""
        mock_audit.return_value = None
        
        mock_query = MagicMock()
        mock_query.execute.return_value.data = None
        mock_query.delete.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_supabase.return_value.table.return_value = mock_query
        
        response = test_client.delete("/products/1")
        
        assert response.status_code == 204


class TestSalesAPI:
    """Tests for sales API endpoints."""
    
    @patch('app.main.get_supabase_client')
    def test_list_sales_returns_sales(self, mock_supabase, test_client):
        """Test listing sales returns sale data."""
        now = datetime.now(timezone.utc).isoformat()
        sales = [
            {"id": 1, "product_id": 1, "quantity_sold": 2, "total_price": 200.0, "timestamp": now},
            {"id": 2, "product_id": 2, "quantity_sold": 1, "total_price": 150.0, "timestamp": now},
        ]
        
        mock_query = MagicMock()
        mock_query.execute.return_value.data = sales
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.order.return_value = mock_query
        mock_supabase.return_value.table.return_value = mock_query
        
        response = test_client.get("/sales")
        
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
    
    @patch('app.main.log_audit_event')
    @patch('app.main.get_supabase_client')
    def test_create_sale_success(self, mock_supabase, mock_audit, test_client):
        """Test creating a sale successfully."""
        mock_audit.return_value = None
        
        now = datetime.now(timezone.utc).isoformat()
        new_sale = {
            "id": 1,
            "product_id": 1,
            "quantity_sold": 3,
            "total_price": 300.0,
            "timestamp": now
        }
        
        mock_rpc = MagicMock()
        mock_rpc.execute.return_value.data = [new_sale]
        mock_supabase.return_value.rpc.return_value = mock_rpc
        
        response = test_client.post(
            "/sales",
            json={"product_id": 1, "quantity_sold": 3}
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["product_id"] == 1
        assert data["quantity_sold"] == 3


class TestReturnsAPI:
    """Tests for returns API endpoints."""
    
    @patch('app.main.log_audit_event')
    @patch('app.main.get_supabase_client')
    def test_process_return_success(self, mock_supabase, mock_audit, test_client):
        """Test processing a return successfully."""
        mock_audit.return_value = None
        
        product = {"id": 1, "name": "Product", "price": 100.0, "quantity": 10}
        now = datetime.now(timezone.utc).isoformat()
        return_record = {
            "id": 1,
            "product_id": 1,
            "quantity_sold": -2,
            "total_price": -200.0,
            "timestamp": now,
            "store_id": "test-store-id"
        }
        
        mock_supabase_instance = MagicMock()
        
        # Product query
        product_query = MagicMock()
        product_query.execute.return_value.data = product
        product_query.select.return_value = product_query
        product_query.eq.return_value = product_query
        product_query.single.return_value = product_query
        
        # Update query
        update_query = MagicMock()
        update_query.execute.return_value.data = [{"quantity": 12}]
        update_query.update.return_value = update_query
        update_query.eq.return_value = update_query
        
        # Insert query for return record
        insert_query = MagicMock()
        insert_query.execute.return_value.data = [return_record]
        insert_query.insert.return_value = insert_query
        
        call_count = [0]
        def table_router(table_name):
            if table_name == "products":
                if call_count[0] == 0:
                    call_count[0] += 1
                    return product_query
                else:
                    return update_query
            elif table_name == "sales":
                return insert_query
            return MagicMock()
        
        mock_supabase_instance.table = table_router
        mock_supabase.return_value = mock_supabase_instance
        
        response = test_client.post(
            "/returns",
            json={"product_id": 1, "quantity_returned": 2}
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["quantity_returned"] == 2
        assert data["refund_amount"] == 200.0


class TestReportsAPI:
    """Tests for reports API endpoints."""
    
    @patch('app.main.get_supabase_client')
    def test_get_reports_success(self, mock_supabase, test_client):
        """Test getting daily report."""
        now = datetime.now(timezone.utc)
        sales = [
            {"id": 1, "product_id": 1, "quantity_sold": 2, "total_price": 200.0, "timestamp": now.isoformat()},
        ]
        products = [{"id": 1, "cost_price": 50.0}]
        
        mock_supabase_instance = MagicMock()
        
        sales_query = MagicMock()
        sales_query.execute.return_value.data = sales
        sales_query.select.return_value = sales_query
        sales_query.eq.return_value = sales_query
        sales_query.gte.return_value = sales_query
        sales_query.lte.return_value = sales_query
        sales_query.order.return_value = sales_query
        
        products_query = MagicMock()
        products_query.execute.return_value.data = products
        products_query.select.return_value = products_query
        products_query.eq.return_value = products_query
        
        def table_router(table_name):
            if table_name == "sales":
                return sales_query
            elif table_name == "products":
                return products_query
            return MagicMock()
        
        mock_supabase_instance.table = table_router
        mock_supabase.return_value = mock_supabase_instance
        
        response = test_client.get("/reports")
        
        assert response.status_code == 200
        data = response.json()
        assert "totals" in data
        assert "transactions" in data
        assert data["totals"]["total_revenue"] == 200.0
    
    def test_get_reports_forbidden_for_cashier(self, test_client_cashier):
        """Test that cashiers cannot access reports."""
        response = test_client_cashier.get("/reports")
        
        assert response.status_code == 403


class TestBillingAPI:
    """Tests for billing API endpoints."""
    
    def test_get_billing_config(self):
        """Test getting billing configuration."""
        from app.main import app
        
        client = TestClient(app)
        response = client.get("/billing/config")
        
        assert response.status_code == 200
        data = response.json()
        assert "prices" in data
        assert "pro" in data["prices"]
        assert "business" in data["prices"]
    
    @patch('app.main.get_plan_info')
    def test_get_current_plan(self, mock_plan_info, test_client):
        """Test getting current plan."""
        mock_plan_info.return_value = {
            "plan": "pro",
            "status": "active",
            "is_active": True,
            "is_on_trial": False,
            "limits": {
                "max_products": None,
                "max_users": 3,
                "csv_export": True,
                "low_stock_alerts": True,
            },
            "usage": {"products": 5}
        }
        
        response = test_client.get("/plan")
        
        assert response.status_code == 200
        data = response.json()
        assert data["plan"] == "pro"
        assert data["is_active"] == True


class TestUserManagementAPI:
    """Tests for user management API endpoints."""
    
    @patch('app.main.get_supabase_client')
    def test_list_users_success(self, mock_supabase, test_client):
        """Test listing users."""
        profiles = [
            {"id": "user-1", "name": "Admin", "role": "admin", "store_id": "test-store-id"},
            {"id": "user-2", "name": "Cashier", "role": "cashier", "store_id": "test-store-id"},
        ]
        
        mock_query = MagicMock()
        mock_query.execute.return_value.data = profiles
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_supabase.return_value.table.return_value = mock_query
        
        # Mock httpx for auth API calls
        with patch('httpx.Client') as mock_http:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"email": "test@example.com", "created_at": "2026-01-01T00:00:00Z"}
            mock_http.return_value.__enter__.return_value.get.return_value = mock_response
            
            response = test_client.get("/users")
        
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
    
    def test_list_users_forbidden_for_cashier(self, test_client_cashier):
        """Test that cashiers cannot list users."""
        response = test_client_cashier.get("/users")
        
        assert response.status_code == 403


class TestLowStockAlertsAPI:
    """Tests for low stock alerts API endpoints."""
    
    @patch('app.main.get_store_plan')
    @patch('app.main.get_supabase_client')
    def test_get_low_stock_alerts_success(self, mock_supabase, mock_plan, test_client):
        """Test getting low stock alerts."""
        from app.subscriptions import PlanLimits
        
        mock_plan.return_value = PlanLimits("pro", status="active")
        
        low_stock_products = [
            {"id": 1, "name": "Low Stock Item", "quantity": 5, "sku": "LS1"},
        ]
        
        mock_query = MagicMock()
        mock_query.execute.return_value.data = low_stock_products
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.lte.return_value = mock_query
        mock_query.order.return_value = mock_query
        mock_supabase.return_value.table.return_value = mock_query
        
        response = test_client.get("/alerts/low-stock?threshold=10")
        
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["quantity"] == 5
    
    @patch('app.main.get_store_plan')
    def test_low_stock_alerts_blocked_for_free_plan(self, mock_plan, test_client):
        """Test that free plan cannot access low stock alerts."""
        from app.subscriptions import PlanLimits
        
        mock_plan.return_value = PlanLimits("free", status="active")
        
        response = test_client.get("/alerts/low-stock")
        
        assert response.status_code == 402
        assert "Pro or Business plan" in response.json()["detail"]


class TestAnalyticsAPI:
    """Tests for analytics API endpoints."""
    
    @patch('app.main.get_store_plan')
    @patch('app.main.get_analytics')
    def test_get_analytics_success(self, mock_analytics, mock_plan, test_client):
        """Test getting analytics."""
        from app.subscriptions import PlanLimits
        from app.analytics import AnalyticsSummary
        
        mock_plan.return_value = PlanLimits("pro", status="active")
        mock_analytics.return_value = AnalyticsSummary(
            period_days=30,
            total_revenue=1000.0,
            total_profit=400.0,
            total_sales=10,
            avg_transaction_value=100.0,
            profit_margin=40.0
        )
        
        response = test_client.get("/analytics?days=30")
        
        assert response.status_code == 200
        data = response.json()
        assert data["total_revenue"] == 1000.0
        assert data["total_profit"] == 400.0
    
    @patch('app.main.get_store_plan')
    def test_analytics_blocked_for_free_plan(self, mock_plan, test_client):
        """Test that free plan cannot access advanced analytics."""
        from app.subscriptions import PlanLimits
        
        mock_plan.return_value = PlanLimits("free", status="active")
        
        response = test_client.get("/analytics")
        
        assert response.status_code == 402


class TestPasswordEncryption:
    """Tests for password encryption/decryption functions."""
    
    def test_encrypt_decrypt_round_trip(self):
        """Test that encrypting and decrypting returns original password."""
        from app.main import _encrypt_password, _decrypt_password
        
        original_password = "TestPassword123!"
        encrypted = _encrypt_password(original_password)
        decrypted = _decrypt_password(encrypted)
        
        assert decrypted == original_password
    
    def test_encrypted_password_is_different(self):
        """Test that encrypted password is different from original."""
        from app.main import _encrypt_password
        
        original_password = "TestPassword123!"
        encrypted = _encrypt_password(original_password)
        
        assert encrypted != original_password


class TestAuditLogsAPI:
    """Tests for audit logs API endpoints."""
    
    @patch('app.main.get_store_plan')
    @patch('app.main.get_supabase_client')
    def test_get_audit_logs_success(self, mock_supabase, mock_plan, test_client):
        """Test getting audit logs for business plan."""
        from app.subscriptions import PlanLimits
        
        mock_plan.return_value = PlanLimits("business", status="active")
        
        now = datetime.now(timezone.utc).isoformat()
        audit_logs = [
            {"id": 1, "user_id": "user-1", "action": "create", "resource_type": "product", "resource_id": "1", "timestamp": now},
        ]
        
        mock_query = MagicMock()
        mock_query.execute.return_value.data = audit_logs
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.order.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_supabase.return_value.table.return_value = mock_query
        
        response = test_client.get("/audit-logs")
        
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
    
    @patch('app.main.get_store_plan')
    def test_audit_logs_blocked_for_pro_plan(self, mock_plan, test_client):
        """Test that pro plan cannot access audit logs."""
        from app.subscriptions import PlanLimits
        
        mock_plan.return_value = PlanLimits("pro", status="active")
        
        response = test_client.get("/audit-logs")
        
        assert response.status_code == 402
        assert "Business plan" in response.json()["detail"]


class TestCSVExportAPI:
    """Tests for CSV export API endpoints."""
    
    @patch('app.main.get_store_plan')
    @patch('app.main.get_supabase_client')
    def test_csv_export_success(self, mock_supabase, mock_plan, test_client):
        """Test CSV export for pro plan."""
        from app.subscriptions import PlanLimits
        
        mock_plan.return_value = PlanLimits("pro", status="active")
        
        now = datetime.now(timezone.utc).isoformat()
        sales = [
            {"id": 1, "product_id": 1, "quantity_sold": 2, "total_price": 200.0, "timestamp": now},
        ]
        products = [{"id": 1, "name": "Product 1", "sku": "P1"}]
        
        mock_supabase_instance = MagicMock()
        
        sales_query = MagicMock()
        sales_query.execute.return_value.data = sales
        sales_query.select.return_value = sales_query
        sales_query.eq.return_value = sales_query
        sales_query.gte.return_value = sales_query
        sales_query.lte.return_value = sales_query
        sales_query.order.return_value = sales_query
        
        products_query = MagicMock()
        products_query.execute.return_value.data = products
        products_query.select.return_value = products_query
        products_query.eq.return_value = products_query
        
        def table_router(table_name):
            if table_name == "sales":
                return sales_query
            elif table_name == "products":
                return products_query
            return MagicMock()
        
        mock_supabase_instance.table = table_router
        mock_supabase.return_value = mock_supabase_instance
        
        response = test_client.get("/reports/export")
        
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/csv; charset=utf-8"
        assert "attachment" in response.headers["content-disposition"]
    
    @patch('app.main.get_store_plan')
    def test_csv_export_blocked_for_free_plan(self, mock_plan, test_client):
        """Test that free plan cannot export CSV."""
        from app.subscriptions import PlanLimits
        
        mock_plan.return_value = PlanLimits("free", status="active")
        
        response = test_client.get("/reports/export")
        
        assert response.status_code == 402
