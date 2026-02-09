"""
Unit tests for analytics calculations.
Tests the analytics module functions and data processing.
"""
import os
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestGetAnalytics:
    """Tests for get_analytics function."""
    
    @patch('app.analytics.get_supabase_client')
    def test_returns_empty_analytics_when_no_sales(self, mock_get_supabase):
        """Test that analytics returns zeros when there are no sales."""
        from app.analytics import get_analytics
        
        mock_supabase = MagicMock()
        mock_query = MagicMock()
        mock_query.execute.return_value.data = []
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.gte.return_value = mock_query
        mock_query.lte.return_value = mock_query
        mock_supabase.table.return_value = mock_query
        mock_get_supabase.return_value = mock_supabase
        
        result = get_analytics("test-store-id", days=30)
        
        assert result.total_revenue == 0
        assert result.total_profit == 0
        assert result.total_sales == 0
        assert result.avg_transaction_value == 0
        assert result.profit_margin == 0
        assert result.period_days == 30
    
    @patch('app.analytics.get_supabase_client')
    def test_calculates_total_revenue_correctly(self, mock_get_supabase):
        """Test that total revenue is calculated correctly."""
        from app.analytics import get_analytics
        
        now = datetime.now(timezone.utc)
        sales_data = [
            {"id": 1, "product_id": 1, "quantity_sold": 2, "total_price": 200.0, "timestamp": now.isoformat()},
            {"id": 2, "product_id": 2, "quantity_sold": 1, "total_price": 150.0, "timestamp": now.isoformat()},
            {"id": 3, "product_id": 1, "quantity_sold": 3, "total_price": 300.0, "timestamp": now.isoformat()},
        ]
        products_data = [
            {"id": 1, "name": "Product 1", "sku": "P1", "price": 100.0, "cost_price": 60.0},
            {"id": 2, "name": "Product 2", "sku": "P2", "price": 150.0, "cost_price": 90.0},
        ]
        
        mock_supabase = MagicMock()
        
        # Create mock for sales query
        sales_query = MagicMock()
        sales_query.execute.return_value.data = sales_data
        sales_query.select.return_value = sales_query
        sales_query.eq.return_value = sales_query
        sales_query.gte.return_value = sales_query
        sales_query.lte.return_value = sales_query
        
        # Create mock for products query
        products_query = MagicMock()
        products_query.execute.return_value.data = products_data
        products_query.select.return_value = products_query
        products_query.eq.return_value = products_query
        
        def table_router(table_name):
            if table_name == "sales":
                return sales_query
            elif table_name == "products":
                return products_query
            return MagicMock()
        
        mock_supabase.table = table_router
        mock_get_supabase.return_value = mock_supabase
        
        result = get_analytics("test-store-id", days=30)
        
        # Total revenue: 200 + 150 + 300 = 650
        assert result.total_revenue == 650.0
        assert result.total_sales == 3
    
    @patch('app.analytics.get_supabase_client')
    def test_calculates_profit_correctly(self, mock_get_supabase):
        """Test that profit is calculated as revenue minus cost."""
        from app.analytics import get_analytics
        
        now = datetime.now(timezone.utc)
        # Sale of 2 units at $100 each (total $200), cost is $60 each
        # Profit = $200 - (2 * $60) = $200 - $120 = $80
        sales_data = [
            {"id": 1, "product_id": 1, "quantity_sold": 2, "total_price": 200.0, "timestamp": now.isoformat()},
        ]
        products_data = [
            {"id": 1, "name": "Product 1", "sku": "P1", "price": 100.0, "cost_price": 60.0},
        ]
        
        mock_supabase = MagicMock()
        
        sales_query = MagicMock()
        sales_query.execute.return_value.data = sales_data
        sales_query.select.return_value = sales_query
        sales_query.eq.return_value = sales_query
        sales_query.gte.return_value = sales_query
        sales_query.lte.return_value = sales_query
        
        products_query = MagicMock()
        products_query.execute.return_value.data = products_data
        products_query.select.return_value = products_query
        products_query.eq.return_value = products_query
        
        def table_router(table_name):
            if table_name == "sales":
                return sales_query
            elif table_name == "products":
                return products_query
            return MagicMock()
        
        mock_supabase.table = table_router
        mock_get_supabase.return_value = mock_supabase
        
        result = get_analytics("test-store-id", days=30)
        
        assert result.total_profit == 80.0
    
    @patch('app.analytics.get_supabase_client')
    def test_handles_missing_cost_price(self, mock_get_supabase):
        """Test that missing cost_price defaults to 0."""
        from app.analytics import get_analytics
        
        now = datetime.now(timezone.utc)
        sales_data = [
            {"id": 1, "product_id": 1, "quantity_sold": 2, "total_price": 200.0, "timestamp": now.isoformat()},
        ]
        # Product has no cost_price
        products_data = [
            {"id": 1, "name": "Product 1", "sku": "P1", "price": 100.0, "cost_price": None},
        ]
        
        mock_supabase = MagicMock()
        
        sales_query = MagicMock()
        sales_query.execute.return_value.data = sales_data
        sales_query.select.return_value = sales_query
        sales_query.eq.return_value = sales_query
        sales_query.gte.return_value = sales_query
        sales_query.lte.return_value = sales_query
        
        products_query = MagicMock()
        products_query.execute.return_value.data = products_data
        products_query.select.return_value = products_query
        products_query.eq.return_value = products_query
        
        def table_router(table_name):
            if table_name == "sales":
                return sales_query
            elif table_name == "products":
                return products_query
            return MagicMock()
        
        mock_supabase.table = table_router
        mock_get_supabase.return_value = mock_supabase
        
        result = get_analytics("test-store-id", days=30)
        
        # Profit = Revenue - (cost * qty) = 200 - (0 * 2) = 200
        assert result.total_profit == 200.0
    
    @patch('app.analytics.get_supabase_client')
    def test_calculates_average_transaction_value(self, mock_get_supabase):
        """Test average transaction value calculation."""
        from app.analytics import get_analytics
        
        now = datetime.now(timezone.utc)
        sales_data = [
            {"id": 1, "product_id": 1, "quantity_sold": 1, "total_price": 100.0, "timestamp": now.isoformat()},
            {"id": 2, "product_id": 1, "quantity_sold": 1, "total_price": 200.0, "timestamp": now.isoformat()},
            {"id": 3, "product_id": 1, "quantity_sold": 1, "total_price": 300.0, "timestamp": now.isoformat()},
        ]
        products_data = [{"id": 1, "name": "Product 1", "price": 100.0, "cost_price": 50.0}]
        
        mock_supabase = MagicMock()
        
        sales_query = MagicMock()
        sales_query.execute.return_value.data = sales_data
        sales_query.select.return_value = sales_query
        sales_query.eq.return_value = sales_query
        sales_query.gte.return_value = sales_query
        sales_query.lte.return_value = sales_query
        
        products_query = MagicMock()
        products_query.execute.return_value.data = products_data
        products_query.select.return_value = products_query
        products_query.eq.return_value = products_query
        
        def table_router(table_name):
            if table_name == "sales":
                return sales_query
            elif table_name == "products":
                return products_query
            return MagicMock()
        
        mock_supabase.table = table_router
        mock_get_supabase.return_value = mock_supabase
        
        result = get_analytics("test-store-id", days=30)
        
        # Average: (100 + 200 + 300) / 3 = 200
        assert result.avg_transaction_value == 200.0
    
    @patch('app.analytics.get_supabase_client')
    def test_identifies_best_and_worst_days(self, mock_get_supabase):
        """Test that best and worst days are correctly identified."""
        from app.analytics import get_analytics
        
        now = datetime.now(timezone.utc)
        yesterday = now - timedelta(days=1)
        day_before = now - timedelta(days=2)
        
        sales_data = [
            # Today: $500
            {"id": 1, "product_id": 1, "quantity_sold": 5, "total_price": 500.0, "timestamp": now.isoformat()},
            # Yesterday: $200
            {"id": 2, "product_id": 1, "quantity_sold": 2, "total_price": 200.0, "timestamp": yesterday.isoformat()},
            # Day before: $1000 (best day)
            {"id": 3, "product_id": 1, "quantity_sold": 10, "total_price": 1000.0, "timestamp": day_before.isoformat()},
        ]
        products_data = [{"id": 1, "name": "Product 1", "price": 100.0, "cost_price": 50.0}]
        
        mock_supabase = MagicMock()
        
        sales_query = MagicMock()
        sales_query.execute.return_value.data = sales_data
        sales_query.select.return_value = sales_query
        sales_query.eq.return_value = sales_query
        sales_query.gte.return_value = sales_query
        sales_query.lte.return_value = sales_query
        
        products_query = MagicMock()
        products_query.execute.return_value.data = products_data
        products_query.select.return_value = products_query
        products_query.eq.return_value = products_query
        
        def table_router(table_name):
            if table_name == "sales":
                return sales_query
            elif table_name == "products":
                return products_query
            return MagicMock()
        
        mock_supabase.table = table_router
        mock_get_supabase.return_value = mock_supabase
        
        result = get_analytics("test-store-id", days=30)
        
        assert result.best_day_revenue == 1000.0
        assert result.worst_day_revenue == 200.0
    
    @patch('app.analytics.get_supabase_client')
    def test_generates_sales_trends_for_all_days(self, mock_get_supabase):
        """Test that sales trends include all days in period, even zero-sales days."""
        from app.analytics import get_analytics
        
        now = datetime.now(timezone.utc)
        # Only one sale 3 days ago
        three_days_ago = now - timedelta(days=3)
        
        sales_data = [
            {"id": 1, "product_id": 1, "quantity_sold": 1, "total_price": 100.0, "timestamp": three_days_ago.isoformat()},
        ]
        products_data = [{"id": 1, "name": "Product 1", "price": 100.0, "cost_price": 50.0}]
        
        mock_supabase = MagicMock()
        
        sales_query = MagicMock()
        sales_query.execute.return_value.data = sales_data
        sales_query.select.return_value = sales_query
        sales_query.eq.return_value = sales_query
        sales_query.gte.return_value = sales_query
        sales_query.lte.return_value = sales_query
        
        products_query = MagicMock()
        products_query.execute.return_value.data = products_data
        products_query.select.return_value = products_query
        products_query.eq.return_value = products_query
        
        def table_router(table_name):
            if table_name == "sales":
                return sales_query
            elif table_name == "products":
                return products_query
            return MagicMock()
        
        mock_supabase.table = table_router
        mock_get_supabase.return_value = mock_supabase
        
        result = get_analytics("test-store-id", days=7)
        
        # Should have 8 days (7 full days + today)
        assert len(result.sales_trends) >= 7
        
        # Most days should have zero revenue
        zero_days = [t for t in result.sales_trends if t.revenue == 0]
        assert len(zero_days) >= 6
    
    @patch('app.analytics.get_supabase_client')
    def test_generates_top_products(self, mock_get_supabase):
        """Test that top products are correctly identified and sorted."""
        from app.analytics import get_analytics
        
        now = datetime.now(timezone.utc)
        sales_data = [
            # Product 1: $500 total
            {"id": 1, "product_id": 1, "quantity_sold": 5, "total_price": 500.0, "timestamp": now.isoformat()},
            # Product 2: $300 total
            {"id": 2, "product_id": 2, "quantity_sold": 3, "total_price": 300.0, "timestamp": now.isoformat()},
            # Product 3: $1000 total (top product)
            {"id": 3, "product_id": 3, "quantity_sold": 10, "total_price": 1000.0, "timestamp": now.isoformat()},
        ]
        products_data = [
            {"id": 1, "name": "Product 1", "sku": "P1", "price": 100.0, "cost_price": 50.0},
            {"id": 2, "name": "Product 2", "sku": "P2", "price": 100.0, "cost_price": 50.0},
            {"id": 3, "name": "Product 3", "sku": "P3", "price": 100.0, "cost_price": 50.0},
        ]
        
        mock_supabase = MagicMock()
        
        sales_query = MagicMock()
        sales_query.execute.return_value.data = sales_data
        sales_query.select.return_value = sales_query
        sales_query.eq.return_value = sales_query
        sales_query.gte.return_value = sales_query
        sales_query.lte.return_value = sales_query
        
        products_query = MagicMock()
        products_query.execute.return_value.data = products_data
        products_query.select.return_value = products_query
        products_query.eq.return_value = products_query
        
        def table_router(table_name):
            if table_name == "sales":
                return sales_query
            elif table_name == "products":
                return products_query
            return MagicMock()
        
        mock_supabase.table = table_router
        mock_get_supabase.return_value = mock_supabase
        
        result = get_analytics("test-store-id", days=30)
        
        assert len(result.top_products) == 3
        # Top product should be Product 3 with $1000
        assert result.top_products[0].total_revenue == 1000.0
        assert result.top_products[0].name == "Product 3"
    
    @patch('app.analytics.get_supabase_client')
    def test_generates_hourly_breakdown(self, mock_get_supabase):
        """Test hourly breakdown is generated for all 24 hours."""
        from app.analytics import get_analytics
        
        now = datetime.now(timezone.utc)
        sales_data = [
            {"id": 1, "product_id": 1, "quantity_sold": 1, "total_price": 100.0, "timestamp": now.isoformat()},
        ]
        products_data = [{"id": 1, "name": "Product 1", "price": 100.0, "cost_price": 50.0}]
        
        mock_supabase = MagicMock()
        
        sales_query = MagicMock()
        sales_query.execute.return_value.data = sales_data
        sales_query.select.return_value = sales_query
        sales_query.eq.return_value = sales_query
        sales_query.gte.return_value = sales_query
        sales_query.lte.return_value = sales_query
        
        products_query = MagicMock()
        products_query.execute.return_value.data = products_data
        products_query.select.return_value = products_query
        products_query.eq.return_value = products_query
        
        def table_router(table_name):
            if table_name == "sales":
                return sales_query
            elif table_name == "products":
                return products_query
            return MagicMock()
        
        mock_supabase.table = table_router
        mock_get_supabase.return_value = mock_supabase
        
        result = get_analytics("test-store-id", days=30)
        
        # Should have 24 hours
        assert len(result.hourly_breakdown) == 24
        
        # Hours should be 0-23
        hours = [h.hour for h in result.hourly_breakdown]
        assert hours == list(range(24))


class TestAnalyticsEdgeCases:
    """Test edge cases in analytics calculations."""
    
    @patch('app.analytics.get_supabase_client')
    def test_handles_database_error_for_sales(self, mock_get_supabase):
        """Test graceful handling of database errors."""
        from app.analytics import get_analytics
        
        mock_supabase = MagicMock()
        mock_query = MagicMock()
        mock_query.execute.side_effect = Exception("Database error")
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.gte.return_value = mock_query
        mock_query.lte.return_value = mock_query
        mock_supabase.table.return_value = mock_query
        mock_get_supabase.return_value = mock_supabase
        
        result = get_analytics("test-store-id", days=30)
        
        # Should return empty analytics on error
        assert result.total_revenue == 0
        assert result.total_sales == 0
    
    @patch('app.analytics.get_supabase_client')
    def test_handles_invalid_timestamp_format(self, mock_get_supabase):
        """Test handling of invalid timestamp formats."""
        from app.analytics import get_analytics
        
        sales_data = [
            {"id": 1, "product_id": 1, "quantity_sold": 1, "total_price": 100.0, "timestamp": "invalid-date"},
            {"id": 2, "product_id": 1, "quantity_sold": 1, "total_price": 200.0, "timestamp": "2026-02-09T10:00:00Z"},
        ]
        products_data = [{"id": 1, "name": "Product 1", "price": 100.0, "cost_price": 50.0}]
        
        mock_supabase = MagicMock()
        
        sales_query = MagicMock()
        sales_query.execute.return_value.data = sales_data
        sales_query.select.return_value = sales_query
        sales_query.eq.return_value = sales_query
        sales_query.gte.return_value = sales_query
        sales_query.lte.return_value = sales_query
        
        products_query = MagicMock()
        products_query.execute.return_value.data = products_data
        products_query.select.return_value = products_query
        products_query.eq.return_value = products_query
        
        def table_router(table_name):
            if table_name == "sales":
                return sales_query
            elif table_name == "products":
                return products_query
            return MagicMock()
        
        mock_supabase.table = table_router
        mock_get_supabase.return_value = mock_supabase
        
        # Should not raise, just skip the invalid timestamp
        result = get_analytics("test-store-id", days=30)
        
        # Total revenue should still be calculated (skipping invalid ones)
        assert result.total_revenue >= 0
    
    @patch('app.analytics.get_supabase_client')
    def test_handles_returns_negative_quantity(self, mock_get_supabase):
        """Test handling of returns (negative quantity)."""
        from app.analytics import get_analytics
        
        now = datetime.now(timezone.utc)
        sales_data = [
            # Regular sale
            {"id": 1, "product_id": 1, "quantity_sold": 5, "total_price": 500.0, "timestamp": now.isoformat()},
            # Return (negative)
            {"id": 2, "product_id": 1, "quantity_sold": -2, "total_price": -200.0, "timestamp": now.isoformat()},
        ]
        products_data = [{"id": 1, "name": "Product 1", "price": 100.0, "cost_price": 50.0}]
        
        mock_supabase = MagicMock()
        
        sales_query = MagicMock()
        sales_query.execute.return_value.data = sales_data
        sales_query.select.return_value = sales_query
        sales_query.eq.return_value = sales_query
        sales_query.gte.return_value = sales_query
        sales_query.lte.return_value = sales_query
        
        products_query = MagicMock()
        products_query.execute.return_value.data = products_data
        products_query.select.return_value = products_query
        products_query.eq.return_value = products_query
        
        def table_router(table_name):
            if table_name == "sales":
                return sales_query
            elif table_name == "products":
                return products_query
            return MagicMock()
        
        mock_supabase.table = table_router
        mock_get_supabase.return_value = mock_supabase
        
        result = get_analytics("test-store-id", days=30)
        
        # Net revenue: 500 - 200 = 300
        assert result.total_revenue == 300.0
