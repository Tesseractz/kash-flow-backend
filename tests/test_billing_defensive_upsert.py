"""Tests for the defensive subscriptions upsert in app.api.routers.billing.

When PostgREST complains that a column doesn't exist (PGRST204), the upsert
should retry with the offending column stripped — so a stale DB schema never
takes down the live billing flow.
"""
from unittest.mock import MagicMock

import pytest

from app.api.routers.billing import _safe_subscription_upsert


class _MissingColumnError(Exception):
    """Mimics the message shape of postgrest.exceptions.APIError for PGRST204."""

    def __init__(self, columns):
        cols = ", ".join(f"'{c}'" for c in columns)
        super().__init__(
            f"Could not find the {cols} column of 'subscriptions' in the schema cache"
        )


class TestSafeSubscriptionUpsert:

    def test_passthrough_on_success(self):
        supa = MagicMock()
        payload = {"store_id": "s1", "plan": "pro", "status": "active"}

        _safe_subscription_upsert(supa, payload)

        supa.table.assert_called_once_with("subscriptions")
        supa.table.return_value.upsert.assert_called_once_with(payload)

    def test_retries_with_missing_column_stripped(self):
        supa = MagicMock()
        upsert_mock = supa.table.return_value.upsert
        # First call raises PGRST204 for a single missing column; second succeeds.
        upsert_mock.return_value.execute.side_effect = [
            _MissingColumnError(["trial_consumed_at"]),
            MagicMock(),
        ]

        payload = {
            "store_id": "s1",
            "plan": "pro",
            "status": "active",
            "trial_consumed_at": "2026-05-12T00:00:00Z",
        }
        _safe_subscription_upsert(supa, payload)

        # Two attempts: original payload, then payload without the missing column.
        assert upsert_mock.call_count == 2
        first_payload, second_payload = upsert_mock.call_args_list[0].args[0], upsert_mock.call_args_list[1].args[0]
        assert "trial_consumed_at" in first_payload
        assert "trial_consumed_at" not in second_payload
        assert second_payload["store_id"] == "s1"

    def test_strips_multiple_missing_columns_across_retries(self):
        """PostgREST errors name one missing column at a time; the helper
        should keep stripping and retrying until the upsert succeeds."""
        supa = MagicMock()
        upsert_mock = supa.table.return_value.upsert
        upsert_mock.return_value.execute.side_effect = [
            _MissingColumnError(["trial_consumed_at"]),
            _MissingColumnError(["paystack_email_token"]),
            MagicMock(),
        ]

        payload = {
            "store_id": "s1",
            "plan": "pro",
            "trial_consumed_at": "x",
            "paystack_email_token": "y",
            "current_period_end": "z",
        }
        _safe_subscription_upsert(supa, payload)

        assert upsert_mock.call_count == 3
        final_payload = upsert_mock.call_args_list[-1].args[0]
        assert set(final_payload.keys()) == {"store_id", "plan", "current_period_end"}

    def test_reraises_non_column_errors(self):
        """A real failure (not a missing column) must propagate."""
        supa = MagicMock()
        supa.table.return_value.upsert.return_value.execute.side_effect = RuntimeError(
            "connection refused",
        )

        with pytest.raises(RuntimeError, match="connection refused"):
            _safe_subscription_upsert(supa, {"store_id": "s1"})

    def test_reraises_when_only_missing_columns_remain(self):
        """If stripping the missing columns would leave an empty payload, re-raise."""
        supa = MagicMock()
        supa.table.return_value.upsert.return_value.execute.side_effect = _MissingColumnError(
            ["store_id"]
        )

        with pytest.raises(Exception):
            _safe_subscription_upsert(supa, {"store_id": "s1"})
