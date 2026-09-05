"""Tests for Admin Controls > Inception Sync: app/core/deps.
require_admin_email and app/services/inception_admin_sync_service.py. No
real network or database — matches this suite's existing convention (see
tests/test_inception.py) of fake session doubles.
"""
import asyncio
from datetime import date

import pytest


# ── router wiring ────────────────────────────────────────────────────────

def test_admin_sync_route_registered_as_post():
    from app.main import create_app

    app = create_app()
    routes = {r.path: r.methods for r in app.routes if hasattr(r, "path")}
    assert "/inception/admin/sync-lmv-metrics" in routes
    assert "POST" in routes["/inception/admin/sync-lmv-metrics"]


# ── app.core.deps.require_admin_email ────────────────────────────────────

def _current_user(email: str):
    from app.core.deps import CurrentUser
    return CurrentUser(
        user_id="u1", tenant_id="t1", schema_name="hari_dss", role="owner",
        name="Hari", email=email, phone_number="",
    )


def test_require_admin_email_allows_the_admin_account():
    from app.core.deps import require_admin_email

    user = asyncio.run(require_admin_email(_current_user("sundarhari10@gmail.com")))
    assert user.email == "sundarhari10@gmail.com"


def test_require_admin_email_is_case_insensitive():
    from app.core.deps import require_admin_email

    user = asyncio.run(require_admin_email(_current_user("SundarHari10@Gmail.com")))
    assert user.email == "SundarHari10@Gmail.com"


def test_require_admin_email_rejects_everyone_else():
    from app.core.deps import require_admin_email
    from app.exceptions import AdminOnlyError

    with pytest.raises(AdminOnlyError):
        asyncio.run(require_admin_email(_current_user("someone.else@gmail.com")))


# ── app.services.inception_admin_sync_service.sync_lmv_metrics_to_eod_bar ───

class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeConnection:
    def __init__(self):
        self.calls = []  # [(stmt_str, bind_dict), ...] — one call per batch

    async def execute(self, stmt, bind):
        self.calls.append((str(stmt), bind))
        # bind has 3 keys per row (iidN/tdN/valN) — see _bulk_update_column.
        return _FakeRowcountResult(len(bind) // 3)


class _FakeRowcountResult:
    def __init__(self, rowcount):
        self.rowcount = rowcount


class _FakeTenantSession:
    """Dispatches by the queried table's name (found in the compiled
    statement text) rather than call order — sync_lmv_metrics_to_eod_bar
    now runs two independent sync paths (_sync_lmv_metrics,
    _sync_opening_range) that each issue their own queries, in an order
    that's an implementation detail, not a contract worth pinning tests to.
    *or_capture_rows* defaults to [] — most of these tests only exercise
    the Metric/LmvDailySnapshot path."""

    def __init__(self, metric_rows, stock_rows, snapshot_rows, or_capture_rows=None):
        self._by_table = {
            "Metric": metric_rows,
            "Stock": stock_rows,
            "LmvDailySnapshot": snapshot_rows,
            "OpeningRangeCapture": or_capture_rows or [],
        }

    async def execute(self, stmt):
        text = str(stmt)
        for table, rows in self._by_table.items():
            if f'FROM "{table}"' in text:
                return _FakeResult(rows)
        raise AssertionError(f"unexpected query, no fake table match: {text}")


class _FakeCentralSession:
    def __init__(self, instrument_rows):
        self._instrument_rows = instrument_rows
        self.connection_obj = _FakeConnection()
        self.committed = False

    async def execute(self, stmt):
        return _FakeResult(self._instrument_rows)

    async def connection(self):
        return self.connection_obj

    async def commit(self):
        self.committed = True


def test_sync_writes_one_lmv_value_into_every_roll_series_sharing_the_underlying():
    """One LmvDailySnapshot "Avg Rate" value for bare-symbol "ADANIENT"
    must update BOTH Instrument rows sharing that underlying_symbol
    ('ADANIENT_I' and 'ADANIENT_II') — see the service's own docstring for
    why (the figure describes the underlying stock, not one specific roll
    series)."""
    from app.services.inception_admin_sync_service import sync_lmv_metrics_to_eod_bar

    metric_rows = [(23, "Avg Rate")]
    stock_rows = [(1, "ADANIENT")]
    snapshot_rows = [(date(2026, 8, 31), 1, 23, 1234.5)]
    instrument_rows = [(101, "ADANIENT"), (102, "ADANIENT")]  # _I and _II

    tenant = _FakeTenantSession(metric_rows, stock_rows, snapshot_rows)
    central = _FakeCentralSession(instrument_rows)

    result = asyncio.run(sync_lmv_metrics_to_eod_bar(tenant, central))

    assert result["symbols_matched"] == 1
    assert result["date_from"] == date(2026, 8, 31)
    assert result["date_to"] == date(2026, 8, 31)
    avg_rate_metric = next(m for m in result["metrics"] if m["column"] == "avg_rate")
    assert avg_rate_metric["candidate_rows"] == 2  # both _I and _II
    assert avg_rate_metric["rows_updated"] == 2
    assert central.committed is True

    # Exactly one batched UPDATE call was issued for avg_rate (well under
    # _UPDATE_BATCH_SIZE), with both instrument ids and the same value.
    assert len(central.connection_obj.calls) == 1
    stmt_str, bind = central.connection_obj.calls[0]
    assert 'UPDATE "EodBar" SET avg_rate = v.val' in stmt_str
    iids = {v for k, v in bind.items() if k.startswith("iid")}
    vals = {v for k, v in bind.items() if k.startswith("val")}
    tds = {v for k, v in bind.items() if k.startswith("td")}
    assert iids == {101, 102}
    assert vals == {1234.5}
    assert tds == {date(2026, 8, 31)}


def test_sync_skips_dated_contract_stock_rows():
    """hari_dss.Stock rows with a digit in their symbol (a dated futures
    contract, e.g. "ADANIENT 29-Sep-2026") must never be treated as the
    plain underlying — only bare symbols are eligible to match an
    Instrument's underlying_symbol."""
    from app.services.inception_admin_sync_service import sync_lmv_metrics_to_eod_bar

    metric_rows = [(23, "Avg Rate")]
    # The dated-contract row is simply absent from what the query returns
    # (the service's own WHERE clause excludes it) — simulate that here.
    stock_rows = [(1, "ADANIENT")]
    snapshot_rows = [(date(2026, 8, 31), 1, 23, 100.0)]
    instrument_rows = [(101, "ADANIENT")]

    tenant = _FakeTenantSession(metric_rows, stock_rows, snapshot_rows)
    central = _FakeCentralSession(instrument_rows)
    result = asyncio.run(sync_lmv_metrics_to_eod_bar(tenant, central))
    assert result["metrics"][0]["candidate_rows"] == 1


def test_sync_skips_snapshot_rows_with_no_matching_instrument():
    """A LmvDailySnapshot value for a symbol with no corresponding
    Instrument row (e.g. an index LMV tracks that Inception doesn't) is
    silently skipped, not an error."""
    from app.services.inception_admin_sync_service import sync_lmv_metrics_to_eod_bar

    metric_rows = [(23, "Avg Rate")]
    stock_rows = [(1, "NIFTY")]
    snapshot_rows = [(date(2026, 8, 31), 1, 23, 100.0)]
    instrument_rows = []  # no Instrument for "NIFTY"

    tenant = _FakeTenantSession(metric_rows, stock_rows, snapshot_rows)
    central = _FakeCentralSession(instrument_rows)
    result = asyncio.run(sync_lmv_metrics_to_eod_bar(tenant, central))
    assert result["metrics"][0]["candidate_rows"] == 0
    assert result["metrics"][0]["rows_updated"] == 0
    assert result["symbols_matched"] == 0


def test_sync_skips_null_values():
    from app.services.inception_admin_sync_service import sync_lmv_metrics_to_eod_bar

    metric_rows = [(23, "Avg Rate")]
    stock_rows = [(1, "ADANIENT")]
    snapshot_rows = [(date(2026, 8, 31), 1, 23, None)]
    instrument_rows = [(101, "ADANIENT")]

    tenant = _FakeTenantSession(metric_rows, stock_rows, snapshot_rows)
    central = _FakeCentralSession(instrument_rows)
    result = asyncio.run(sync_lmv_metrics_to_eod_bar(tenant, central))
    assert result["metrics"][0]["candidate_rows"] == 0


def test_sync_no_registered_metrics_is_a_clean_noop():
    from app.services.inception_admin_sync_service import sync_lmv_metrics_to_eod_bar

    tenant = _FakeTenantSession([], [], [])
    central = _FakeCentralSession([])
    result = asyncio.run(sync_lmv_metrics_to_eod_bar(tenant, central))
    assert result == {"metrics": [], "date_from": None, "date_to": None, "symbols_matched": 0}


def test_options_oi_and_market_profile_metrics_use_the_same_lmv_snapshot_path():
    """callstrikehighestoi/Max Pain/VAH/POC/VAL aren't turnover/ATP figures,
    but they're archived to LmvDailySnapshot exactly the same way — same
    _sync_lmv_metrics code path, just more _METRIC_TO_COLUMN entries."""
    from app.services.inception_admin_sync_service import sync_lmv_metrics_to_eod_bar

    metric_rows = [(50, "Max Pain"), (51, "VAH")]
    stock_rows = [(1, "ADANIENT")]
    snapshot_rows = [
        (date(2026, 9, 4), 1, 50, 9999.0),
        (date(2026, 9, 4), 1, 51, 2500.0),
    ]
    instrument_rows = [(101, "ADANIENT")]

    tenant = _FakeTenantSession(metric_rows, stock_rows, snapshot_rows)
    central = _FakeCentralSession(instrument_rows)
    result = asyncio.run(sync_lmv_metrics_to_eod_bar(tenant, central))

    columns = {m["column"]: m for m in result["metrics"]}
    assert columns["max_pain"]["rows_updated"] == 1
    assert columns["vah"]["rows_updated"] == 1


# ── OR.High/OR.Low: separate OpeningRangeCapture path ────────────────────

def test_sync_opening_range_writes_into_every_roll_series_sharing_the_underlying():
    """Same fan-out rule as the Metric-based path (see the analogous Avg
    Rate test above), but sourced from OpeningRangeCapture instead of
    LmvDailySnapshot — OR.High/OR.Low describe the underlying stock's
    opening window, not one specific roll series."""
    from app.services.inception_admin_sync_service import sync_lmv_metrics_to_eod_bar

    stock_rows = [(1, "ADANIENT")]
    or_capture_rows = [(date(2026, 9, 4), 1, 3050.0, 3010.0)]  # trade_date, stock_id, high, low
    instrument_rows = [(101, "ADANIENT"), (102, "ADANIENT")]  # _I and _II

    tenant = _FakeTenantSession([], stock_rows, [], or_capture_rows=or_capture_rows)
    central = _FakeCentralSession(instrument_rows)
    result = asyncio.run(sync_lmv_metrics_to_eod_bar(tenant, central))

    columns = {m["column"]: m for m in result["metrics"]}
    assert columns["or_high"]["candidate_rows"] == 2  # both _I and _II
    assert columns["or_high"]["rows_updated"] == 2
    assert columns["or_low"]["candidate_rows"] == 2
    assert columns["or_low"]["rows_updated"] == 2
    assert result["date_from"] == date(2026, 9, 4)
    assert result["symbols_matched"] == 1


def test_sync_opening_range_skips_rows_with_no_matching_instrument():
    from app.services.inception_admin_sync_service import sync_lmv_metrics_to_eod_bar

    stock_rows = [(1, "NIFTY")]
    or_capture_rows = [(date(2026, 9, 4), 1, 25000.0, 24800.0)]
    tenant = _FakeTenantSession([], stock_rows, [], or_capture_rows=or_capture_rows)
    central = _FakeCentralSession([])  # no Instrument for "NIFTY"

    result = asyncio.run(sync_lmv_metrics_to_eod_bar(tenant, central))
    columns = {m["column"]: m for m in result["metrics"]}
    assert columns["or_high"]["candidate_rows"] == 0
    assert columns["or_low"]["candidate_rows"] == 0


def test_sync_merges_metric_and_opening_range_results_into_one_response():
    """The two sync paths (LmvDailySnapshot-based metrics, OpeningRange
    Capture-based OR.High/OR.Low) share one commit and one combined
    response — symbols_matched is the union of instruments either path
    actually touched, not a double count or just one path's number."""
    from app.services.inception_admin_sync_service import sync_lmv_metrics_to_eod_bar

    metric_rows = [(23, "Avg Rate")]
    stock_rows = [(1, "ADANIENT"), (2, "RELIANCE")]
    snapshot_rows = [(date(2026, 9, 1), 1, 23, 500.0)]
    or_capture_rows = [(date(2026, 9, 4), 2, 2900.0, 2870.0)]
    instrument_rows = [(101, "ADANIENT"), (201, "RELIANCE")]

    tenant = _FakeTenantSession(metric_rows, stock_rows, snapshot_rows, or_capture_rows=or_capture_rows)
    central = _FakeCentralSession(instrument_rows)
    result = asyncio.run(sync_lmv_metrics_to_eod_bar(tenant, central))

    columns = {m["column"] for m in result["metrics"]}
    assert {"avg_rate", "or_high", "or_low"} <= columns
    assert result["date_from"] == date(2026, 9, 1)
    assert result["date_to"] == date(2026, 9, 4)
    assert result["symbols_matched"] == 2  # ADANIENT (metric path) + RELIANCE (OR path)
    assert central.committed is True
