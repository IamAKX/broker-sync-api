import asyncio
from datetime import date

import pytest


def test_snapshot_range_route_registered():
    from app.main import create_app

    app = create_app()
    paths = {r.path for r in app.routes if hasattr(r, "path")}
    assert "/lmv-snapshot/range" in paths


def test_snapshot_range_response_reuses_stock_snapshot_schema():
    from app.schemas.historic import SnapshotRangeResponse, SnapshotResponse, StockSnapshot

    resp = SnapshotRangeResponse(
        days=[
            SnapshotResponse(
                trade_date="2026-01-05",
                stocks=[StockSnapshot(symbol="INFY", display_name="INFY", metrics={"High": 1810.5})],
            )
        ]
    )
    assert resp.days[0].stocks[0].metrics == {"High": 1810.5}


# ── lmv_snapshot_service.get_snapshot_range ─────────────────────────────────
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
    from app.services import lmv_snapshot_service

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

    monkeypatch.setattr(lmv_snapshot_service, "fetch_recent_trade_dates", fake_fetch_recent_trade_dates)
    monkeypatch.setattr(lmv_snapshot_service, "fetch_snapshot_rows_for_dates", fake_fetch_snapshot_rows_for_dates)

    result = asyncio.run(lmv_snapshot_service.get_snapshot_range(session=None, days=2))

    assert [day.trade_date for day in result.days] == [d1, d2]
    assert result.days[0].stocks[0].metrics["High"] == 1800.0
    assert result.days[1].stocks[0].metrics["High"] == 1810.0


def test_get_snapshot_range_handles_a_day_with_no_rows(monkeypatch):
    """A trade_date can come back from fetch_recent_trade_dates with zero
    matching rows in the batch fetch (e.g. a symbol filter mismatch upstream)
    — must still produce an empty-stocks day rather than a KeyError."""
    from app.services import lmv_snapshot_service

    d1 = date(2026, 1, 5)

    async def fake_fetch_recent_trade_dates(session, limit):
        return [d1]

    async def fake_fetch_snapshot_rows_for_dates(session, trade_dates):
        return []

    monkeypatch.setattr(lmv_snapshot_service, "fetch_recent_trade_dates", fake_fetch_recent_trade_dates)
    monkeypatch.setattr(lmv_snapshot_service, "fetch_snapshot_rows_for_dates", fake_fetch_snapshot_rows_for_dates)

    result = asyncio.run(lmv_snapshot_service.get_snapshot_range(session=None, days=1))

    assert result.days[0].trade_date == d1
    assert result.days[0].stocks == []


def test_get_snapshot_range_returns_empty_when_no_trade_dates(monkeypatch):
    from app.services import lmv_snapshot_service

    async def fake_fetch_recent_trade_dates(session, limit):
        return []

    monkeypatch.setattr(lmv_snapshot_service, "fetch_recent_trade_dates", fake_fetch_recent_trade_dates)

    result = asyncio.run(lmv_snapshot_service.get_snapshot_range(session=None, days=5))

    assert result.days == []


def test_get_snapshot_range_rejects_days_below_one():
    from app.exceptions import InvalidDateRangeError
    from app.services import lmv_snapshot_service

    with pytest.raises(InvalidDateRangeError):
        asyncio.run(lmv_snapshot_service.get_snapshot_range(session=None, days=0))


def test_get_snapshot_range_rejects_days_over_max():
    from app.exceptions import InvalidDateRangeError
    from app.services import lmv_snapshot_service

    with pytest.raises(InvalidDateRangeError):
        asyncio.run(lmv_snapshot_service.get_snapshot_range(session=None, days=91))


# ── get_snapshot_range_payload: serialization-ready dict + thread offload ─────

def test_get_snapshot_range_payload_returns_plain_dict(monkeypatch):
    from app.services import lmv_snapshot_service

    d1, d2 = date(2026, 1, 5), date(2026, 1, 6)

    async def fake_dates(session, limit):
        return [d1, d2]

    async def fake_rows(session, trade_dates):
        return [
            _row("INFY", "INFY", d1, "High", 1800.0),
            _row("INFY", "INFY", d2, "High", 1810.0),
        ]

    async def _not_ready(session):
        return False

    monkeypatch.setattr(lmv_snapshot_service, "fetch_recent_trade_dates", fake_dates)
    monkeypatch.setattr(lmv_snapshot_service, "fetch_snapshot_rows_for_dates", fake_rows)
    monkeypatch.setattr(lmv_snapshot_service, "wide_table_ready", _not_ready)

    out = asyncio.run(lmv_snapshot_service.get_snapshot_range_payload(session=None, days=2))

    assert isinstance(out, dict)
    assert [d["trade_date"] for d in out["days"]] == ["2026-01-05", "2026-01-06"]
    assert out["days"][0]["stocks"][0] == {"symbol": "INFY", "display_name": "INFY", "metrics": {"High": 1800.0}}


def test_get_snapshot_range_payload_empty(monkeypatch):
    from app.services import lmv_snapshot_service

    async def fake_dates(session, limit):
        return []

    monkeypatch.setattr(lmv_snapshot_service, "fetch_recent_trade_dates", fake_dates)
    assert asyncio.run(lmv_snapshot_service.get_snapshot_range_payload(session=None, days=5)) == {"days": []}


def test_get_snapshot_range_payload_rejects_bad_days():
    from app.exceptions import InvalidDateRangeError
    from app.services import lmv_snapshot_service

    with pytest.raises(InvalidDateRangeError):
        asyncio.run(lmv_snapshot_service.get_snapshot_range_payload(session=None, days=0))
    with pytest.raises(InvalidDateRangeError):
        asyncio.run(lmv_snapshot_service.get_snapshot_range_payload(session=None, days=999))


def test_range_payload_reads_wide_table_when_ready(monkeypatch):
    from app.services import lmv_snapshot_service

    d1, d2 = date(2026, 1, 5), date(2026, 1, 6)

    async def fake_dates(session, limit):
        return [d1, d2]

    async def ready(session):
        return True

    async def fake_wide(session, trade_dates):
        assert trade_dates == [d1, d2]
        return [
            {"trade_date": d1, "symbol": "INFY", "display_name": "Infosys", "metrics": {"High": 1800.0}},
            {"trade_date": d2, "symbol": "INFY", "display_name": "Infosys", "metrics": {"High": 1810.0}},
        ]

    def _boom(*a, **k):
        raise AssertionError("EAV pivot path should not run when the wide table is ready")

    monkeypatch.setattr(lmv_snapshot_service, "fetch_recent_trade_dates", fake_dates)
    monkeypatch.setattr(lmv_snapshot_service, "wide_table_ready", ready)
    monkeypatch.setattr(lmv_snapshot_service, "fetch_wide_rows_for_dates", fake_wide)
    monkeypatch.setattr(lmv_snapshot_service, "fetch_snapshot_rows_for_dates", _boom)

    out = asyncio.run(lmv_snapshot_service.get_snapshot_range_payload(session=None, days=2))

    assert out == {
        "days": [
            {"trade_date": "2026-01-05", "stocks": [
                {"symbol": "INFY", "display_name": "Infosys", "metrics": {"High": 1800.0}}]},
            {"trade_date": "2026-01-06", "stocks": [
                {"symbol": "INFY", "display_name": "Infosys", "metrics": {"High": 1810.0}}]},
        ]
    }


# ── lmv_snapshot_repo.upsert_wide_for_date: JSONB merge, not replace ────────
# (issue #50) — a second same-day upload whose metrics dict is NARROWER than
# an earlier one (e.g. a live-computed column like CWTO that couldn't be
# resolved that particular tick) must not erase the earlier upload's columns
# from the wide read model. No real Postgres here (same rationale as every
# other repo test in this suite) — asserts on the actual generated SQL's
# shape instead of a live merge result.

def test_upsert_wide_for_date_merges_metrics_instead_of_replacing(monkeypatch):
    import asyncio as _asyncio
    from datetime import date as _date
    from sqlalchemy.dialects import postgresql
    from app.repositories import lmv_snapshot_repo
    from app.models.tenant import LmvDailySnapshotWide

    captured = {}

    class _FakeResult:
        rowcount = 1

    class _FakeSession:
        async def execute(self, stmt):
            captured["stmt"] = stmt
            return _FakeResult()

    rows = [{
        "stock_id": 1, "symbol": "ABB", "display_name": "ABB",
        "metrics": {"CWTO": 12.5},
    }]
    _asyncio.run(lmv_snapshot_repo.upsert_wide_for_date(_FakeSession(), _date(2026, 9, 18), rows))

    compiled = str(captured["stmt"].compile(dialect=postgresql.dialect()))
    # The metrics column must be combined with the JSONB || operator
    # against the TABLE's own existing value, not just assigned the new
    # payload's value outright — that's what makes an omitted key survive.
    assert '"LmvDailySnapshotWide".metrics || excluded.metrics' in compiled
    assert "metrics = excluded.metrics" not in compiled   # the old (bug) shape


def test_upsert_wide_for_date_no_rows_is_noop():
    import asyncio as _asyncio
    from datetime import date as _date
    from app.repositories import lmv_snapshot_repo

    class _FakeSession:
        async def execute(self, stmt):
            raise AssertionError("should never execute anything for an empty batch")

    result = _asyncio.run(lmv_snapshot_repo.upsert_wide_for_date(_FakeSession(), _date(2026, 9, 18), []))
    assert result == 0


# ── lmv_snapshot_service.upsert_lmv_snapshot: skips null-valued metrics ────
# (issue #52) — a metric explicitly sent as null (e.g. a still-uncomputed
# live-overlay column like CWTO on a client that hasn't picked up the
# client-side omit-don't-null fix yet) must not overwrite a previously-good
# value for the same (trade_date, stock, metric) in either the EAV table
# or the wide read model — defense in depth alongside the client fix (see
# services.scheduled_jobs._build_lmv_snapshot_payload in the client repo).

def test_upsert_lmv_snapshot_skips_none_valued_metrics(monkeypatch):
    import asyncio
    from datetime import date
    from app.schemas.historic import UploadRow
    from app.schemas.lmv_snapshot import LmvSnapshotUploadRequest
    from app.services import lmv_snapshot_service

    async def fake_is_holiday(session, trade_date):
        return False

    async def fake_bulk_get_or_create_stocks(session, pairs):
        return {symbol: i + 1 for i, (symbol, _display) in enumerate(pairs)}

    captured = {}

    async def fake_bulk_get_or_create_metrics(session, metric_types):
        captured["metric_types"] = dict(metric_types)
        return {name: i + 1 for i, name in enumerate(metric_types)}

    async def fake_bulk_upsert_lmv_snapshot_values(session, value_rows):
        captured["value_rows"] = value_rows
        return len(value_rows)

    async def fake_upsert_wide_for_date(session, trade_date, wide_rows):
        captured["wide_rows"] = wide_rows
        return len(wide_rows)

    monkeypatch.setattr(lmv_snapshot_service, "is_holiday", fake_is_holiday)
    monkeypatch.setattr(lmv_snapshot_service, "bulk_get_or_create_stocks", fake_bulk_get_or_create_stocks)
    monkeypatch.setattr(lmv_snapshot_service, "bulk_get_or_create_metrics", fake_bulk_get_or_create_metrics)
    monkeypatch.setattr(lmv_snapshot_service, "bulk_upsert_lmv_snapshot_values", fake_bulk_upsert_lmv_snapshot_values)
    monkeypatch.setattr(lmv_snapshot_service, "upsert_wide_for_date", fake_upsert_wide_for_date)

    class _FakeSession:
        async def commit(self):
            pass

    payload = LmvSnapshotUploadRequest(
        trade_date=date(2026, 9, 18),
        rows=[UploadRow(symbol="ABB", display_name="ABB", metrics={"CLOSE": 100.0, "CWTO": None})],
    )

    asyncio.run(lmv_snapshot_service.upsert_lmv_snapshot(_FakeSession(), payload))

    assert "CWTO" not in captured["metric_types"]
    assert "CLOSE" in captured["metric_types"]
    assert len(captured["value_rows"]) == 1   # only CLOSE, no row at all for the null CWTO
    assert captured["wide_rows"][0]["metrics"] == {"CLOSE": 100.0}
    assert "CWTO" not in captured["wide_rows"][0]["metrics"]


def test_upsert_lmv_snapshot_all_null_metrics_for_a_stock_uploads_nothing_for_it(monkeypatch):
    """The degenerate case: every metric for a stock is null this upload
    (e.g. a symbol with no live tick at all yet) — must not crash, and
    must produce zero EAV rows and an empty wide metrics dict for it."""
    import asyncio
    from datetime import date
    from app.schemas.historic import UploadRow
    from app.schemas.lmv_snapshot import LmvSnapshotUploadRequest
    from app.services import lmv_snapshot_service

    async def fake_is_holiday(session, trade_date):
        return False

    async def fake_bulk_get_or_create_stocks(session, pairs):
        return {symbol: 1 for symbol, _display in pairs}

    captured = {}

    async def fake_bulk_get_or_create_metrics(session, metric_types):
        captured["metric_types"] = dict(metric_types)
        return {}

    async def fake_bulk_upsert_lmv_snapshot_values(session, value_rows):
        captured["value_rows"] = value_rows
        return len(value_rows)

    async def fake_upsert_wide_for_date(session, trade_date, wide_rows):
        captured["wide_rows"] = wide_rows
        return len(wide_rows)

    monkeypatch.setattr(lmv_snapshot_service, "is_holiday", fake_is_holiday)
    monkeypatch.setattr(lmv_snapshot_service, "bulk_get_or_create_stocks", fake_bulk_get_or_create_stocks)
    monkeypatch.setattr(lmv_snapshot_service, "bulk_get_or_create_metrics", fake_bulk_get_or_create_metrics)
    monkeypatch.setattr(lmv_snapshot_service, "bulk_upsert_lmv_snapshot_values", fake_bulk_upsert_lmv_snapshot_values)
    monkeypatch.setattr(lmv_snapshot_service, "upsert_wide_for_date", fake_upsert_wide_for_date)

    class _FakeSession:
        async def commit(self):
            pass

    payload = LmvSnapshotUploadRequest(
        trade_date=date(2026, 9, 18),
        rows=[UploadRow(symbol="ABB", display_name="ABB", metrics={"CWTO": None})],
    )

    asyncio.run(lmv_snapshot_service.upsert_lmv_snapshot(_FakeSession(), payload))

    assert captured["metric_types"] == {}
    assert captured["value_rows"] == []
    assert captured["wide_rows"][0]["metrics"] == {}
