"""
Tests for Categories, Customers, and Discounts API endpoints
"""
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from datetime import datetime, timezone, timedelta

from app.main import app
from app.deps import get_current_context, RequestContext


# Helper to create admin context
def make_admin_context():
    return RequestContext(
        user_id="test-admin-id",
        store_id="test-store-id",
        role="admin"
    )


def make_cashier_context():
    return RequestContext(
        user_id="test-cashier-id",
        store_id="test-store-id",
        role="cashier"
    )


class TestCategoriesAPI:
    """Tests for /categories endpoints"""

    def test_list_categories_success(self):
        """Test listing categories"""
        mock_categories = [
            {"id": "cat-1", "store_id": "test-store-id", "name": "Beverages", "color": "#6366f1"},
            {"id": "cat-2", "store_id": "test-store-id", "name": "Snacks", "color": "#22c55e"},
        ]
        
        app.dependency_overrides[get_current_context] = make_admin_context
        
        with patch('app.main.get_supabase_client') as mock_sb:
            mock_client = MagicMock()
            mock_sb.return_value = mock_client
            mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.order.return_value.order.return_value.execute.return_value.data = mock_categories
            
            client = TestClient(app)
            response = client.get("/categories")
            
            assert response.status_code == 200
            assert len(response.json()) == 2
        
        app.dependency_overrides.clear()

    def test_create_category_success(self):
        """Test creating a category"""
        new_category = {"name": "Electronics", "color": "#3b82f6"}
        created_category = {"id": "cat-new", "store_id": "test-store-id", **new_category}
        
        app.dependency_overrides[get_current_context] = make_admin_context
        
        with patch('app.main.get_supabase_client') as mock_sb:
            mock_client = MagicMock()
            mock_sb.return_value = mock_client
            mock_client.table.return_value.insert.return_value.execute.return_value.data = [created_category]
            
            client = TestClient(app)
            response = client.post("/categories", json=new_category)
            
            assert response.status_code == 201
            assert response.json()["name"] == "Electronics"
        
        app.dependency_overrides.clear()

    def test_create_category_forbidden_for_cashier(self):
        """Test that cashiers cannot create categories"""
        app.dependency_overrides[get_current_context] = make_cashier_context
        
        client = TestClient(app)
        response = client.post("/categories", json={"name": "Test"})
        
        assert response.status_code == 403
        
        app.dependency_overrides.clear()

    def test_update_category_success(self):
        """Test updating a category"""
        updated_category = {"id": "cat-1", "store_id": "test-store-id", "name": "Updated Name", "color": "#ef4444"}
        
        app.dependency_overrides[get_current_context] = make_admin_context
        
        with patch('app.main.get_supabase_client') as mock_sb:
            mock_client = MagicMock()
            mock_sb.return_value = mock_client
            mock_client.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value.data = [updated_category]
            
            client = TestClient(app)
            response = client.put("/categories/cat-1", json={"name": "Updated Name"})
            
            assert response.status_code == 200
            assert response.json()["name"] == "Updated Name"
        
        app.dependency_overrides.clear()

    def test_delete_category_success(self):
        """Test deleting a category"""
        app.dependency_overrides[get_current_context] = make_admin_context
        
        with patch('app.main.get_supabase_client') as mock_sb:
            mock_client = MagicMock()
            mock_sb.return_value = mock_client
            mock_client.table.return_value.delete.return_value.eq.return_value.eq.return_value.execute.return_value = None
            
            client = TestClient(app)
            response = client.delete("/categories/cat-1")
            
            assert response.status_code == 204
        
        app.dependency_overrides.clear()


class TestCustomersAPI:
    """Tests for /customers endpoints"""

    def test_list_customers_success(self):
        """Test listing customers"""
        mock_customers = [
            {"id": "cust-1", "store_id": "test-store-id", "name": "John Doe", "email": "john@example.com"},
            {"id": "cust-2", "store_id": "test-store-id", "name": "Jane Smith", "phone": "+27123456789"},
        ]
        
        app.dependency_overrides[get_current_context] = make_admin_context
        
        with patch('app.main.get_supabase_client') as mock_sb:
            mock_client = MagicMock()
            mock_sb.return_value = mock_client
            mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.order.return_value.execute.return_value.data = mock_customers
            
            client = TestClient(app)
            response = client.get("/customers")
            
            assert response.status_code == 200
            assert len(response.json()) == 2
        
        app.dependency_overrides.clear()

    def test_get_customer_success(self):
        """Test getting a single customer"""
        mock_customer = {"id": "cust-1", "store_id": "test-store-id", "name": "John Doe", "loyalty_points": 100}
        
        app.dependency_overrides[get_current_context] = make_admin_context
        
        with patch('app.main.get_supabase_client') as mock_sb:
            mock_client = MagicMock()
            mock_sb.return_value = mock_client
            mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.single.return_value.execute.return_value.data = mock_customer
            
            client = TestClient(app)
            response = client.get("/customers/cust-1")
            
            assert response.status_code == 200
            assert response.json()["name"] == "John Doe"
        
        app.dependency_overrides.clear()

    def test_create_customer_success(self):
        """Test creating a customer"""
        new_customer = {"name": "New Customer", "email": "new@example.com", "phone": "+27111222333"}
        created_customer = {"id": "cust-new", "store_id": "test-store-id", **new_customer, "loyalty_points": 0}
        
        app.dependency_overrides[get_current_context] = make_admin_context
        
        with patch('app.main.get_supabase_client') as mock_sb:
            mock_client = MagicMock()
            mock_sb.return_value = mock_client
            mock_client.table.return_value.insert.return_value.execute.return_value.data = [created_customer]
            
            client = TestClient(app)
            response = client.post("/customers", json=new_customer)
            
            assert response.status_code == 201
            assert response.json()["name"] == "New Customer"
        
        app.dependency_overrides.clear()

    def test_create_customer_forbidden_for_cashier(self):
        """Test that cashiers cannot create customers"""
        app.dependency_overrides[get_current_context] = make_cashier_context
        
        client = TestClient(app)
        response = client.post("/customers", json={"name": "Test"})
        
        assert response.status_code == 403
        
        app.dependency_overrides.clear()

    def test_update_customer_success(self):
        """Test updating a customer"""
        updated_customer = {"id": "cust-1", "store_id": "test-store-id", "name": "Updated Name", "email": "updated@example.com"}
        
        app.dependency_overrides[get_current_context] = make_admin_context
        
        with patch('app.main.get_supabase_client') as mock_sb:
            mock_client = MagicMock()
            mock_sb.return_value = mock_client
            mock_client.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value.data = [updated_customer]
            
            client = TestClient(app)
            response = client.put("/customers/cust-1", json={"name": "Updated Name"})
            
            assert response.status_code == 200
        
        app.dependency_overrides.clear()

    def test_delete_customer_success(self):
        """Test deleting a customer"""
        app.dependency_overrides[get_current_context] = make_admin_context
        
        with patch('app.main.get_supabase_client') as mock_sb:
            mock_client = MagicMock()
            mock_sb.return_value = mock_client
            mock_client.table.return_value.delete.return_value.eq.return_value.eq.return_value.execute.return_value = None
            
            client = TestClient(app)
            response = client.delete("/customers/cust-1")
            
            assert response.status_code == 204
        
        app.dependency_overrides.clear()

    def test_add_loyalty_points_success(self):
        """Test adding loyalty points to a customer"""
        app.dependency_overrides[get_current_context] = make_admin_context
        
        with patch('app.main.get_supabase_client') as mock_sb:
            mock_client = MagicMock()
            mock_sb.return_value = mock_client
            # First call to get current points
            mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.single.return_value.execute.return_value.data = {"loyalty_points": 50}
            # Second call to update - include all required Customer fields
            mock_client.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value.data = [{
                "id": "cust-1",
                "store_id": "test-store-id",
                "name": "John Doe",
                "loyalty_points": 60,
                "total_spent": 1000,
                "total_visits": 5,
                "is_active": True,
            }]
            
            client = TestClient(app)
            response = client.post("/customers/cust-1/add-points?points=10")
            
            assert response.status_code == 200
            assert response.json()["loyalty_points"] == 60
        
        app.dependency_overrides.clear()

    def test_get_customer_purchases(self):
        """Test getting customer purchase history"""
        mock_purchases = [
            {"id": 1, "product_id": 1, "quantity_sold": 2, "total_price": 200, "timestamp": "2026-02-09T10:00:00Z"},
        ]
        
        app.dependency_overrides[get_current_context] = make_admin_context
        
        with patch('app.main.get_supabase_client') as mock_sb:
            mock_client = MagicMock()
            mock_sb.return_value = mock_client
            mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = mock_purchases
            
            client = TestClient(app)
            response = client.get("/customers/cust-1/purchases")
            
            assert response.status_code == 200
            assert len(response.json()) == 1
        
        app.dependency_overrides.clear()


class TestDiscountsAPI:
    """Tests for /discounts endpoints"""

    def test_list_discounts_success(self):
        """Test listing discounts"""
        mock_discounts = [
            {"id": "disc-1", "store_id": "test-store-id", "name": "10% Off", "discount_type": "percentage", "discount_value": 10, "is_active": True},
            {"id": "disc-2", "store_id": "test-store-id", "name": "R50 Off", "discount_type": "fixed", "discount_value": 50, "is_active": True},
        ]
        
        app.dependency_overrides[get_current_context] = make_admin_context
        
        with patch('app.main.get_supabase_client') as mock_sb:
            mock_client = MagicMock()
            mock_sb.return_value = mock_client
            mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.order.return_value.execute.return_value.data = mock_discounts
            
            client = TestClient(app)
            response = client.get("/discounts")
            
            assert response.status_code == 200
            assert len(response.json()) == 2
        
        app.dependency_overrides.clear()

    def test_list_discounts_forbidden_for_cashier(self):
        """Test that cashiers cannot list discounts"""
        app.dependency_overrides[get_current_context] = make_cashier_context
        
        client = TestClient(app)
        response = client.get("/discounts")
        
        assert response.status_code == 403
        
        app.dependency_overrides.clear()

    def test_create_discount_percentage_success(self):
        """Test creating a percentage discount"""
        new_discount = {
            "name": "Summer Sale",
            "discount_type": "percentage",
            "discount_value": 20,
            "code": "SUMMER20"
        }
        created_discount = {"id": "disc-new", "store_id": "test-store-id", **new_discount, "usage_count": 0}
        
        app.dependency_overrides[get_current_context] = make_admin_context
        
        with patch('app.main.get_supabase_client') as mock_sb:
            mock_client = MagicMock()
            mock_sb.return_value = mock_client
            mock_client.table.return_value.insert.return_value.execute.return_value.data = [created_discount]
            
            client = TestClient(app)
            response = client.post("/discounts", json=new_discount)
            
            assert response.status_code == 201
            assert response.json()["name"] == "Summer Sale"
        
        app.dependency_overrides.clear()

    def test_create_discount_fixed_success(self):
        """Test creating a fixed amount discount"""
        new_discount = {
            "name": "R100 Off",
            "discount_type": "fixed",
            "discount_value": 100,
            "min_purchase_amount": 500
        }
        created_discount = {"id": "disc-new", "store_id": "test-store-id", **new_discount}
        
        app.dependency_overrides[get_current_context] = make_admin_context
        
        with patch('app.main.get_supabase_client') as mock_sb:
            mock_client = MagicMock()
            mock_sb.return_value = mock_client
            mock_client.table.return_value.insert.return_value.execute.return_value.data = [created_discount]
            
            client = TestClient(app)
            response = client.post("/discounts", json=new_discount)
            
            assert response.status_code == 201
        
        app.dependency_overrides.clear()

    def test_create_discount_percentage_exceeds_100(self):
        """Test that percentage discount cannot exceed 100%"""
        new_discount = {
            "name": "Invalid",
            "discount_type": "percentage",
            "discount_value": 150
        }
        
        app.dependency_overrides[get_current_context] = make_admin_context
        
        client = TestClient(app)
        response = client.post("/discounts", json=new_discount)
        
        assert response.status_code == 400
        assert "100%" in response.json()["detail"]
        
        app.dependency_overrides.clear()

    def test_apply_discount_success(self):
        """Test applying a discount code"""
        mock_discount = {
            "id": "disc-1",
            "store_id": "test-store-id",
            "name": "20% Off",
            "code": "SAVE20",
            "discount_type": "percentage",
            "discount_value": 20,
            "min_purchase_amount": 0,
            "is_active": True,
            "usage_limit": None,
            "usage_count": 0,
        }
        
        app.dependency_overrides[get_current_context] = make_admin_context
        
        with patch('app.main.get_supabase_client') as mock_sb:
            mock_client = MagicMock()
            mock_sb.return_value = mock_client
            mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.single.return_value.execute.return_value.data = mock_discount
            
            client = TestClient(app)
            response = client.post("/discounts/apply", json={
                "code": "SAVE20",
                "cart_total": 500
            })
            
            assert response.status_code == 200
            data = response.json()
            assert data["discount_amount"] == 100  # 20% of 500
            assert data["final_total"] == 400
        
        app.dependency_overrides.clear()

    def test_apply_discount_invalid_code(self):
        """Test applying an invalid discount code"""
        app.dependency_overrides[get_current_context] = make_admin_context
        
        with patch('app.main.get_supabase_client') as mock_sb:
            mock_client = MagicMock()
            mock_sb.return_value = mock_client
            mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.single.return_value.execute.return_value.data = None
            
            client = TestClient(app)
            response = client.post("/discounts/apply", json={
                "code": "INVALID",
                "cart_total": 500
            })
            
            assert response.status_code == 404
            assert "Invalid" in response.json()["detail"]
        
        app.dependency_overrides.clear()

    def test_apply_discount_min_purchase_not_met(self):
        """Test applying discount when minimum purchase not met"""
        mock_discount = {
            "id": "disc-1",
            "store_id": "test-store-id",
            "name": "Big Spender",
            "code": "BIGSPEND",
            "discount_type": "fixed",
            "discount_value": 100,
            "min_purchase_amount": 1000,
            "is_active": True,
        }
        
        app.dependency_overrides[get_current_context] = make_admin_context
        
        with patch('app.main.get_supabase_client') as mock_sb:
            mock_client = MagicMock()
            mock_sb.return_value = mock_client
            mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.single.return_value.execute.return_value.data = mock_discount
            
            client = TestClient(app)
            response = client.post("/discounts/apply", json={
                "code": "BIGSPEND",
                "cart_total": 500
            })
            
            assert response.status_code == 400
            assert "Minimum" in response.json()["detail"]
        
        app.dependency_overrides.clear()

    def test_apply_discount_expired(self):
        """Test applying an expired discount"""
        mock_discount = {
            "id": "disc-1",
            "store_id": "test-store-id",
            "name": "Expired Sale",
            "code": "EXPIRED",
            "discount_type": "percentage",
            "discount_value": 10,
            "min_purchase_amount": 0,
            "is_active": True,
            "end_date": "2020-01-01T00:00:00Z",  # Expired
        }
        
        app.dependency_overrides[get_current_context] = make_admin_context
        
        with patch('app.main.get_supabase_client') as mock_sb:
            mock_client = MagicMock()
            mock_sb.return_value = mock_client
            mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.single.return_value.execute.return_value.data = mock_discount
            
            client = TestClient(app)
            response = client.post("/discounts/apply", json={
                "code": "EXPIRED",
                "cart_total": 500
            })
            
            assert response.status_code == 400
            assert "expired" in response.json()["detail"].lower()
        
        app.dependency_overrides.clear()

    def test_apply_discount_with_max_cap(self):
        """Test percentage discount with maximum cap"""
        mock_discount = {
            "id": "disc-1",
            "store_id": "test-store-id",
            "name": "Capped Discount",
            "code": "CAPPED",
            "discount_type": "percentage",
            "discount_value": 50,
            "min_purchase_amount": 0,
            "max_discount_amount": 100,  # Cap at R100
            "is_active": True,
        }
        
        app.dependency_overrides[get_current_context] = make_admin_context
        
        with patch('app.main.get_supabase_client') as mock_sb:
            mock_client = MagicMock()
            mock_sb.return_value = mock_client
            mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.single.return_value.execute.return_value.data = mock_discount
            
            client = TestClient(app)
            response = client.post("/discounts/apply", json={
                "code": "CAPPED",
                "cart_total": 1000  # 50% would be R500, but capped at R100
            })
            
            assert response.status_code == 200
            data = response.json()
            assert data["discount_amount"] == 100  # Capped
            assert data["final_total"] == 900
        
        app.dependency_overrides.clear()

    def test_delete_discount_success(self):
        """Test deleting a discount"""
        app.dependency_overrides[get_current_context] = make_admin_context
        
        with patch('app.main.get_supabase_client') as mock_sb:
            mock_client = MagicMock()
            mock_sb.return_value = mock_client
            mock_client.table.return_value.delete.return_value.eq.return_value.eq.return_value.execute.return_value = None
            
            client = TestClient(app)
            response = client.delete("/discounts/disc-1")
            
            assert response.status_code == 204
        
        app.dependency_overrides.clear()
