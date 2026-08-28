"""A failed subscription lookup must never read as an expired plan.

The old behaviour returned PlanLimits("expired") from a bare except, so every
transient Supabase or network error told a paying customer their subscription
had lapsed — with a clean 200 the client could not distinguish from the real
thing. These tests pin the new contract: no rows means expired, an error means
fail open (degraded), and a readable row is reported as-is.
"""
from unittest.mock import MagicMock, patch

from app.services import subscriptions


def _client_returning(row):
    client = MagicMock()
    q = client.table.return_value.select.return_value.eq.return_value.single.return_value
    q.execute.return_value = MagicMock(data=row)
    return client


def _client_raising(error):
    client = MagicMock()
    q = client.table.return_value.select.return_value.eq.return_value.single.return_value
    q.execute.side_effect = error
    return client


def test_active_pro_row_is_reported_as_active():
    row = {"plan": "pro", "status": "active", "trial_end": None,
           "current_period_end": None, "trial_consumed_at": None}
    with patch.object(subscriptions, "get_supabase_client", return_value=_client_returning(row)):
        limits = subscriptions.get_store_plan("store-1")
    assert limits.is_active is True
    assert limits.degraded is False


def test_missing_subscription_row_really_is_expired():
    err = Exception("JSON object requested, multiple (or no) rows returned")
    err.code = "PGRST116"
    with patch.object(subscriptions, "get_supabase_client", return_value=_client_raising(err)):
        limits = subscriptions.get_store_plan("store-1")
    assert limits.plan == "expired"
    assert limits.is_active is False
    assert limits.degraded is False


def test_transient_error_fails_open_not_expired():
    # The bug this file exists for: a network hiccup reported the store as
    # expired, locking a paying customer out of their own till.
    with patch.object(subscriptions, "get_supabase_client",
                      return_value=_client_raising(ConnectionError("reset by peer"))):
        limits = subscriptions.get_store_plan("store-1")
    assert limits.is_active is True
    assert limits.degraded is True
    assert limits.max_products is None


def test_transient_error_retries_before_failing_open():
    client = MagicMock()
    q = client.table.return_value.select.return_value.eq.return_value.single.return_value
    row = {"plan": "pro", "status": "active", "trial_end": None,
           "current_period_end": None, "trial_consumed_at": None}
    q.execute.side_effect = [ConnectionError("blip"), MagicMock(data=row)]
    with patch.object(subscriptions, "get_supabase_client", return_value=client):
        limits = subscriptions.get_store_plan("store-1")
    # The second attempt succeeded, so this is the real answer, not a stand-in.
    assert limits.is_active is True
    assert limits.degraded is False
    assert q.execute.call_count == 2


def test_plan_info_exposes_degraded_flag():
    with patch.object(subscriptions, "get_supabase_client",
                      return_value=_client_raising(ConnectionError("down"))):
        info = subscriptions.get_plan_info("store-1")
    assert info["degraded"] is True
    assert info["is_active"] is True
