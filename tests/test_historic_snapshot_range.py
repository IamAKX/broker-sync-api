"""Tests for GET /historic/range — historical_service.get_snapshot_range.

Issue #30 (desktop client repo): ExternalImport's "database" source used
to fetch a cold ~100-day lookback window one date at a time (up to ~70
individual HTTP round trips, 8 in flight at once via a ThreadPoolExecutor),
which saturated this backend's small gunicorn worker pool and starved
whatever else was in flight at the same time (the live LMV read, the N-Day
strategy fetch) into a timeout — reported as "the app doesn't respond for
a while" on a strategy's first apply. This bulk endpoint (mirroring
lmv_snapshot's own /lmv-snapshot/range, see test_lmv_snapshot_range.py)
replaces all of those with one request.
"""
import asyncio
from datetime import date

import pytest


def test_snapshot_range_route_registered():
    from app.main import create_app

    app = create_app()
    paths = {r.path for r in app.routes if hasattr(r, "path")}
    assert "/historic/range" in paths


# ── historical_service.get_snapshot_range ────────────────────────────────
# No real Postgres in this test environment (see test_strategies_formula_
# settings.py's repository tests for the same rationale) — the repo fetch
# functions are monkeypatched with fakes so this exercises the service's own
# grouping/pivot/validation logic, not a live query.

def _row(symbol, display_name, trade_date, metric_name, value_number):
    return {
        "symbol": symbol,
        "display_name": display_name,
        "metric_name": metric_name,
        "trade_date": trade_date,
        "value_number": value_number,
        "value_text": None,
    }


def test_get_snapshot_range_groups_rows_by_date_chronologically(monkeypatch):
    from app.services import historical_service

    d1, d2 = date(2026, 1, 5), date(2026, 1, 6)

    async def fake_fetch_recent_trade_dates(session, limit):
        assert limit == 2
        return [d1, d2]

    async def fake_fetch_snapshot_rows_for_dates(session, trade_dates):
        assert trade_dates == [d1, d2]
        return [
            _row("INFY", "INFY", d1, "High", 1800.0),
            _row("INFY", "INFY", d2, "High", 1810.0),
        ]

    monkeypatch.setattr(historical_service, "fetch_recent_trade_dates", fake_fetch_recent_trade_dates)
    monkeypatch.setattr(historical_service, "fetch_snapshot_rows_for_dates", fake_fetch_snapshot_rows_for_dates)

    result = asyncio.run(historical_service.get_snapshot_range(session=None, days=2))

    assert [day.trade_date for day in result.days] == [d1, d2]
    assert result.days[0].stocks[0].metrics["High"] == 1800.0
    assert result.days[1].stocks[0].metrics["High"] == 1810.0


def test_get_snapshot_range_handles_a_day_with_no_rows(monkeypatch):
    """A trade_date can come back from fetch_recent_trade_dates with zero
    matching rows in the batch fetch — must still produce an empty-stocks
    day rather than a KeyError."""
    from app.services import historical_service

    d1 = date(2026, 1, 5)

    async def fake_fetch_recent_trade_dates(session, limit):
        return [d1]

    async def fake_fetch_snapshot_rows_for_dates(session, trade_dates):
        return []

    monkeypatch.setattr(historical_service, "fetch_recent_trade_dates", fake_fetch_recent_trade_dates)
    monkeypatch.setattr(historical_service, "fetch_snapshot_rows_for_dates", fake_fetch_snapshot_rows_for_dates)

    result = asyncio.run(historical_service.get_snapshot_range(session=None, days=1))

    assert result.days[0].trade_date == d1
    assert result.days[0].stocks == []


def test_get_snapshot_range_returns_empty_when_no_trade_dates(monkeypatch):
    from app.services import historical_service

    async def fake_fetch_recent_trade_dates(session, limit):
        return []

    monkeypatch.setattr(historical_service, "fetch_recent_trade_dates", fake_fetch_recent_trade_dates)

    result = asyncio.run(historical_service.get_snapshot_range(session=None, days=5))

    assert result.days == []


def test_get_snapshot_range_rejects_days_below_one():
    from app.exceptions import InvalidDateRangeError
    from app.services import historical_service

    with pytest.raises(InvalidDateRangeError):
        asyncio.run(historical_service.get_snapshot_range(session=None, days=0))


def test_get_snapshot_range_rejects_days_over_max():
    from app.exceptions import InvalidDateRangeError
    from app.services import historical_service

    with pytest.raises(InvalidDateRangeError):
        asyncio.run(historical_service.get_snapshot_range(session=None, days=121))


def test_get_snapshot_range_accepts_the_max_boundary(monkeypatch):
    from app.services import historical_service

    async def fake_fetch_recent_trade_dates(session, limit):
        assert limit == 120
        return []

    monkeypatch.setattr(historical_service, "fetch_recent_trade_dates", fake_fetch_recent_trade_dates)

    result = asyncio.run(historical_service.get_snapshot_range(session=None, days=120))
    assert result.days == []
