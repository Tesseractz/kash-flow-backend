"""Tests for /products pagination + filter combinations.

Pins the X-Total-Count behavior and the single-query count="exact" path so
the numeric-search count bug we just fixed can't regress.
"""
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api.deps import RequestContext, get_current_context


@pytest.fixture
def admin_context():
    return RequestContext(user_id=str(uuid4()), store_id=str(uuid4()), role="admin")


@pytest.fixture
def client():
    return TestClient(app)


def _chainable_query(data, count=None):
    """Return a MagicMock that supports every method the products router chains,
    so calls like `table().select().eq().or_().gte().lte().order().range().execute()`
    flow through to a single result.
    """
    q = MagicMock()
    for method in (
        "select",
        "insert",
        "update",
        "delete",
        "upsert",
        "eq",
        "neq",
        "gte",
        "lte",
        "gt",
        "lt",
        "or_",
        "ilike",
        "order",
        "range",
        "limit",
        "single",
    ):
        getattr(q, method).return_value = q

    result = MagicMock()
    result.data = data
    result.count = count
    q.execute.return_value = result
    return q


class TestProductsPagination:

    @patch("app.db.supabase.get_supabase_client")
    def test_paginated_response_has_total_count_header(self, mock_supabase, client, admin_context):
        mock_supabase.return_value.table.return_value = _chainable_query(
            data=[{"id": 1, "name": "P1", "price": 10.0, "quantity": 1}],
            count=42,
        )
        app.dependency_overrides[get_current_context] = lambda: admin_context
        try:
            resp = client.get("/products?page=1&page_size=10")
            assert resp.status_code == 200
            assert resp.headers.get("x-total-count") == "42"
        finally:
            app.dependency_overrides.clear()

    @patch("app.db.supabase.get_supabase_client")
    def test_unpaginated_response_has_no_total_count_header(self, mock_supabase, client, admin_context):
        mock_supabase.return_value.table.return_value = _chainable_query(
            data=[{"id": 1, "name": "P1", "price": 10.0, "quantity": 1}],
            count=42,
        )
        app.dependency_overrides[get_current_context] = lambda: admin_context
        try:
            resp = client.get("/products")
            assert resp.status_code == 200
            assert "x-total-count" not in {k.lower() for k in resp.headers.keys()}
        finally:
            app.dependency_overrides.clear()

    @patch("app.db.supabase.get_supabase_client")
    def test_numeric_search_uses_or_with_id_eq(self, mock_supabase, client, admin_context):
        """Regression: searching by a numeric query must include id.eq.N in the OR,
        not silently fall back to name-only ilike."""
        query = _chainable_query(
            data=[{"id": 7, "name": "Widget 7", "price": 10.0, "quantity": 1}],
            count=1,
        )
        mock_supabase.return_value.table.return_value = query

        app.dependency_overrides[get_current_context] = lambda: admin_context
        try:
            resp = client.get("/products?q=7&page=1&page_size=10")
            assert resp.status_code == 200
            assert resp.headers.get("x-total-count") == "1"

            # The router must call .or_() with conditions covering name, sku, and id.eq.
            or_calls = query.or_.call_args_list
            assert or_calls, "Expected query.or_() to be called for q=7"
            joined = " ".join(c.args[0] for c in or_calls if c.args)
            assert "name.ilike" in joined
            assert "sku.ilike" in joined
            assert "id.eq.7" in joined
        finally:
            app.dependency_overrides.clear()

    @patch("app.db.supabase.get_supabase_client")
    def test_text_search_does_not_include_id_eq(self, mock_supabase, client, admin_context):
        """Non-numeric q must search name+sku only (id.eq would 400 for text)."""
        query = _chainable_query(
            data=[{"id": 1, "name": "Widget", "price": 10.0, "quantity": 1}],
            count=1,
        )
        mock_supabase.return_value.table.return_value = query

        app.dependency_overrides[get_current_context] = lambda: admin_context
        try:
            resp = client.get("/products?q=widget&page=1&page_size=10")
            assert resp.status_code == 200
            or_calls = query.or_.call_args_list
            joined = " ".join(c.args[0] for c in or_calls if c.args)
            assert "name.ilike" in joined
            assert "sku.ilike" in joined
            assert "id.eq" not in joined
        finally:
            app.dependency_overrides.clear()

    @patch("app.db.supabase.get_supabase_client")
    def test_falls_back_to_data_length_when_count_is_none(self, mock_supabase, client, admin_context):
        """If the driver doesn't surface .count (legacy mock), use len(data)."""
        mock_supabase.return_value.table.return_value = _chainable_query(
            data=[{"id": i, "name": f"P{i}", "price": 1.0, "quantity": 1} for i in range(3)],
            count=None,
        )
        app.dependency_overrides[get_current_context] = lambda: admin_context
        try:
            resp = client.get("/products?page=1&page_size=10")
            assert resp.status_code == 200
            assert resp.headers.get("x-total-count") == "3"
        finally:
            app.dependency_overrides.clear()

    @patch("app.db.supabase.get_supabase_client")
    def test_only_one_query_is_executed(self, mock_supabase, client, admin_context):
        """The new implementation should not run a separate count query."""
        query = _chainable_query(
            data=[{"id": 1, "name": "P", "price": 1.0, "quantity": 1}],
            count=1,
        )
        mock_supabase.return_value.table.return_value = query

        app.dependency_overrides[get_current_context] = lambda: admin_context
        try:
            resp = client.get("/products?page=1&page_size=10")
            assert resp.status_code == 200
            # Single execute() call — old code did one for count and one for data.
            assert query.execute.call_count == 1
        finally:
            app.dependency_overrides.clear()
