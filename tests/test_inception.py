import asyncio
import uuid
from datetime import date

import pytest


# ── Wiring: routes, models ───────────────────────────────────────────────────

def test_inception_routes_registered():
    from app.main import create_app

    app = create_app()
    paths = {r.path for r in app.routes if hasattr(r, "path")}
    for expected in [
        "/inception/availability", "/inception/instruments", "/inception/columns",
        "/inception/snapshot", "/inception/hmv", "/inception/recompute",
        "/inception/strategies", "/inception/strategies/{strategy_id}",
        "/inception/formula-variables", "/inception/formula-variables/{variable_id}",
        "/inception/compile-check",
    ]:
        assert expected in paths


def test_inception_models_registered_on_tenant_base():
    import sys
    for module_name in ["app.models", "app.models.central", "app.models.tenant", "app.db.base"]:
        sys.modules.pop(module_name, None)
    import app.models  # noqa: F401
    from app.db.base import TenantBase

    for table_name in ("InceptionDerivedValue", "InceptionStrategy", "InceptionFormulaVariable"):
        assert table_name in TenantBase.metadata.tables

    idv_cols = {c.name for c in TenantBase.metadata.tables["InceptionDerivedValue"].columns}
    assert idv_cols == {"instrument_id", "trade_date", "metric_id", "value_number", "value_text", "updated_at"}
    # No FK on instrument_id to the central Instrument table — see the
    # model's docstring on why (schema_translate_map would misresolve it).
    idv_table = TenantBase.metadata.tables["InceptionDerivedValue"]
    assert idv_table.c.instrument_id.foreign_keys == set()


def test_central_models_include_instrument_eodbar_tradingcalendar():
    from app.db.base import CentralBase

    for table_name in ("Instrument", "EodBar", "TradingCalendar"):
        assert table_name in CentralBase.metadata.tables


# ── inception_columns ─────────────────────────────────────────────────────────

def test_group_b_has_24_gap_codes_each_producing_3_metrics():
    from app.services.inception_columns import GROUP_B, gap_metric_names

    assert len(GROUP_B) == 24
    for code in GROUP_B:
        names = gap_metric_names(code)
        assert names == [f"{code} LOW", f"{code} HIGH", f"{code} DATE"]


def test_group_a_has_47_codes_and_no_overlap_with_raw_or_group_b():
    from app.services.inception_columns import GROUP_A, GROUP_B, RAW_FIELDS

    assert len(GROUP_A) == 47
    assert not (set(GROUP_A) & set(GROUP_B))
    assert not (set(GROUP_A) & set(RAW_FIELDS))


def test_column_catalogue_flags_gap_columns():
    from app.services.inception_columns import column_catalogue

    catalogue = column_catalogue()
    by_code = {c.code: c for c in catalogue}
    assert by_code["52WH"].stateful_gap is False
    assert by_code["52WH"].group == "derived"
    assert by_code["OPEN"].group == "raw"
    assert by_code["DAY UF GUP 1"].stateful_gap is True
    assert by_code["DAY UF GUP 1"].group == "derived"


# ── inception_formula_engine: Group A ────────────────────────────────────────

def _bar(d, o, h, low, c, vol=1000, oi=500):
    return {"trade_date": d, "open": o, "high": h, "low": low, "close": c, "volume": vol, "open_interest": oi}


def test_compute_group_a_period_bookkeeping():
    """5 hand-traced bars spanning a quarter/year boundary and a financial-
    year boundary (Apr 1) — see the PR description / commit message for the
    full by-hand derivation this asserts against."""
    from app.services.inception_formula_engine import compute_group_a

    bars = [
        _bar(date(2024, 12, 30), 100, 105, 95, 100),
        _bar(date(2024, 12, 31), 101, 106, 96, 102),
        _bar(date(2025, 1, 2), 103, 110, 100, 108),
        _bar(date(2025, 1, 3), 109, 112, 107, 110),
        _bar(date(2025, 4, 1), 111, 115, 109, 113),
    ]
    result = compute_group_a(bars)

    d1, d2, d3, d4, d5 = (b["trade_date"] for b in bars)

    # First bar: nothing "previous" exists yet.
    assert result[d1]["P.OPEN"] is None
    assert result[d1]["ATH"] == 105
    assert result[d1]["ATL"] == 95
    assert result[d1]["CQO"] == 100 and result[d1]["CQH"] == 105 and result[d1]["CQL"] == 95
    assert result[d1]["PQO"] is None

    # Second bar: previous-day fields populate; ATH/ATL/current-quarter extend.
    assert result[d2]["P.OPEN"] == 100 and result[d2]["P.CLOSE"] == 100
    assert result[d2]["% CHG PDC AND OPEN"] == pytest.approx(1.0)
    assert result[d2]["DAY % CHANGE"] == pytest.approx(2.0)
    assert result[d2]["ATH"] == 106 and result[d2]["ATL"] == 95
    assert result[d2]["CQH"] == 106 and result[d2]["CQL"] == 95

    # Third bar: new quarter (Q1 2025) and new year (2025) both reset; FY
    # (Apr-Mar) does NOT reset — Jan 2025 is still FY2024.
    assert result[d3]["CQO"] == 103 and result[d3]["CQH"] == 110 and result[d3]["CQL"] == 100
    assert result[d3]["PQO"] == 100 and result[d3]["PQH"] == 106 and result[d3]["PQL"] == 95 and result[d3]["PQC"] == 102
    assert result[d3]["QT"] == 102 and result[d3]["QB"] == 102
    assert result[d3]["CYO"] == 103 and result[d3]["CYH"] == 110 and result[d3]["CYL"] == 100
    assert result[d3]["PYO"] == 100 and result[d3]["PYC"] == 102
    assert result[d3]["CFYO"] == 100 and result[d3]["CFYH"] == 110 and result[d3]["CFYL"] == 95
    assert result[d3]["PFYO"] is None  # no earlier financial year in the data

    # Fourth bar: same quarter/year/fy as third — accumulators extend, "previous" unchanged.
    assert result[d4]["CQH"] == 112 and result[d4]["CQL"] == 100
    assert result[d4]["PQC"] == 102
    assert result[d4]["CFYH"] == 112 and result[d4]["CFYL"] == 95

    # Fifth bar: crosses Apr 1 — quarter (Q2), and FY (now FY2025) both reset;
    # calendar year does NOT reset (still 2025).
    assert result[d5]["CQO"] == 111 and result[d5]["CQH"] == 115 and result[d5]["CQL"] == 109
    assert result[d5]["PQO"] == 103 and result[d5]["PQH"] == 112 and result[d5]["PQL"] == 100 and result[d5]["PQC"] == 110
    assert result[d5]["QT"] == 110 and result[d5]["QB"] == 102  # max/min(PQC of last 2 quarters)
    assert result[d5]["CYO"] == 103 and result[d5]["CYH"] == 115 and result[d5]["CYL"] == 100  # year unchanged
    assert result[d5]["CFYO"] == 111 and result[d5]["CFYH"] == 115 and result[d5]["CFYL"] == 109
    assert result[d5]["PFYO"] == 100 and result[d5]["PFYH"] == 112 and result[d5]["PFYL"] == 95 and result[d5]["PFYC"] == 110
    assert result[d5]["ATH"] == 115 and result[d5]["ATL"] == 95


def test_compute_group_a_weekly_pwc_updates_only_on_new_week():
    """% CHG PWC AND OPEN uses the previous COMPLETE week's close vs this
    day's open — stays None until a second week exists, then holds constant
    through every day of a week (only changes on the first day of the next
    week)."""
    from app.services.inception_formula_engine import compute_group_a

    # Week 1: Mon 2025-01-06 .. Fri 2025-01-10. Week 2 starts Mon 2025-01-13.
    bars = [
        _bar(date(2025, 1, 6), 100, 101, 99, 100),
        _bar(date(2025, 1, 7), 100, 101, 99, 101),
        _bar(date(2025, 1, 10), 101, 102, 100, 102),  # week 1's close = 102
        _bar(date(2025, 1, 13), 105, 106, 104, 105),  # first day of week 2
        _bar(date(2025, 1, 14), 106, 107, 105, 106),  # still week 2
    ]
    result = compute_group_a(bars)
    d1, d2, d3, d4, d5 = (b["trade_date"] for b in bars)

    assert result[d1]["% CHG PWC AND OPEN"] is None
    assert result[d3]["% CHG PWC AND OPEN"] is None  # still week 1, no prior week yet
    assert result[d4]["% CHG PWC AND OPEN"] == pytest.approx((105 - 102) / 102 * 100)
    assert result[d5]["% CHG PWC AND OPEN"] == pytest.approx((106 - 102) / 102 * 100)  # unchanged within week 2


def test_compute_group_a_52_week_window_evicts_old_bars():
    from app.services.inception_formula_engine import compute_group_a

    bars = [
        _bar(date(2024, 1, 1), 100, 200, 50, 100),   # extreme high/low, > 364 days before the last bar
        _bar(date(2025, 6, 1), 100, 110, 90, 100),
    ]
    result = compute_group_a(bars)
    last = bars[-1]["trade_date"]
    # 2024-01-01's 200/50 extremes are outside the 364-day window by 2025-06-01.
    assert result[last]["52WH"] == 110
    assert result[last]["52WL"] == 90
    # ATH/ATL are never windowed — the 2024 extremes still count.
    assert result[last]["ATH"] == 200
    assert result[last]["ATL"] == 50


# ── inception_formula_engine: Group B ────────────────────────────────────────

def test_compute_group_b_opens_and_fills_a_daily_gap_up():
    from app.services.inception_formula_engine import compute_group_b

    d1, d2, d3 = date(2025, 2, 3), date(2025, 2, 4), date(2025, 2, 5)  # Mon/Tue/Wed, one ISO week
    bars = [
        _bar(d1, 99, 101, 98, 100),
        _bar(d2, 106, 108, 105, 106),  # gap up from 100 -> 106 (6% > 0.5% threshold)
        _bar(d3, 107, 109, 98, 99),    # opens a 2nd gap (106->107); closes back through 100, filling gap 1
    ]
    result = compute_group_b(bars, threshold_pct=0.5)

    assert result[d1]["DAY UF GUP 1"] is None

    low, high, opened_on = result[d3]["DAY FD GUP 1"]
    assert (low, high, opened_on) == (100, 106, d2)   # gap 1: filled

    low, high, opened_on = result[d3]["DAY UF GUP 1"]
    assert (low, high, opened_on) == (106, 107, d3)   # gap 2: still open

    assert result[d3]["DAY UF GUP 2"] is None
    assert result[d3]["DAY UF GDN 1"] is None
    # Only one ISO week of data — no completed-previous-week to report yet.
    assert result[d3]["WEEK UF GUP 1"] is None


def test_compute_group_b_fifo_caps_at_3_most_recent_first():
    from app.services.inception_formula_engine import compute_group_b

    # 5 successive daily gap-ups, none filled — only the 3 most recent survive.
    bars = [_bar(date(2025, 3, 3), 100, 101, 99, 100)]
    price = 100
    for i in range(1, 6):
        price = price * 1.10  # +10% each day, always above threshold
        bars.append(_bar(date(2025, 3, 3 + i), price, price * 1.02, price * 0.98, price))

    result = compute_group_b(bars, threshold_pct=0.5)
    last_day = bars[-1]["trade_date"]
    ranks = [result[last_day][f"DAY UF GUP {r}"] for r in (1, 2, 3)]
    assert all(area is not None for area in ranks)
    opened_dates = [area[2] for area in ranks]
    assert opened_dates == sorted(opened_dates, reverse=True)  # most-recent-first
    assert opened_dates[0] == last_day


def test_compute_group_b_gap_down_uses_mirror_condition():
    from app.services.inception_formula_engine import compute_group_b

    d1, d2 = date(2025, 5, 5), date(2025, 5, 6)
    bars = [
        _bar(d1, 100, 101, 99, 100),
        _bar(d2, 94, 95, 93, 94),  # gap down from 100 (-6%)
    ]
    result = compute_group_b(bars, threshold_pct=0.5)
    low, high, opened_on = result[d2]["DAY UF GDN 1"]
    assert (low, high, opened_on) == (94, 100, d2)
    assert result[d2]["DAY UF GUP 1"] is None


# ── inception_strategy_engine ────────────────────────────────────────────────

def test_evaluate_arithmetic_and_functions():
    from app.services.inception_strategy_engine import evaluate

    tokens = [
        {"type": "func", "value": "MAX("}, {"type": "col", "value": "HIGH"},
        {"type": "op", "value": ","}, {"type": "col", "value": "CLOSE"}, {"type": "paren", "value": ")"},
    ]
    # NOTE: comma is passed straight through as an "op" token value the same
    # way the client encodes function-argument separators.
    row = {"HIGH": 110.0, "CLOSE": 105.0}
    assert evaluate(tokens, row) == 110.0


def test_evaluate_returns_none_for_missing_column():
    from app.services.inception_strategy_engine import evaluate

    tokens = [{"type": "col", "value": "52WH"}, {"type": "op", "value": "+"}, {"type": "num", "value": "1"}]
    assert evaluate(tokens, {}) is None  # None + 1 -> None literal -> TypeError -> caught -> None


def test_evaluate_condition_comparison_and_boolean_ops():
    from app.services.inception_strategy_engine import evaluate_condition

    tokens = [
        {"type": "col", "value": "CLOSE"}, {"type": "op", "value": ">"}, {"type": "col", "value": "P.CLOSE"},
        {"type": "op", "value": "AND"},
        {"type": "col", "value": "DAY % CHANGE"}, {"type": "op", "value": ">"}, {"type": "num", "value": "1"},
    ]
    assert evaluate_condition(tokens, {"CLOSE": 110, "P.CLOSE": 100, "DAY % CHANGE": 10}) is True
    assert evaluate_condition(tokens, {"CLOSE": 90, "P.CLOSE": 100, "DAY % CHANGE": 10}) is False
    assert evaluate_condition([], {"CLOSE": 1}) is True  # empty filter = always true


def test_compile_check_uses_dummy_values_for_raw_fields():
    from app.services.inception_strategy_engine import compile_check

    tokens = [
        {"type": "col", "value": "OPEN"}, {"type": "op", "value": "+"}, {"type": "col", "value": "OPENINT"},
    ]
    ok, error = compile_check(tokens)
    assert ok is True
    assert error is None


def test_compile_check_reports_division_by_zero():
    from app.services.inception_strategy_engine import compile_check

    tokens = [
        {"type": "num", "value": "1"}, {"type": "op", "value": "/"}, {"type": "num", "value": "0"},
    ]
    ok, error = compile_check(tokens)
    assert ok is False
    assert "zero" in error.lower()


def test_apply_strategy_columns_respects_row_filter_and_var_expansion():
    from app.services.inception_strategy_engine import apply_strategy_columns

    strategies = [{
        "id": "s1", "name": "Strong Day",
        "row_filter": [
            {"type": "col", "value": "DAY % CHANGE"}, {"type": "op", "value": ">"}, {"type": "num", "value": "5"},
        ],
        "columns": [{
            "name": "Adjusted Close",
            "formula": [{"type": "col", "value": "CLOSE"}, {"type": "op", "value": "+"}, {"type": "var", "value": "Bump"}],
        }],
    }]
    variables = {"Bump": [{"type": "num", "value": "2"}]}

    strong_row = {"DAY % CHANGE": 10, "CLOSE": 100}
    weak_row = {"DAY % CHANGE": 1, "CLOSE": 100}

    assert apply_strategy_columns(strategies, strong_row, variables) == {"Adjusted Close": 102.0}
    assert apply_strategy_columns(strategies, weak_row, variables) == {}


# ── inception_service: period parsing ───────────────────────────────────────

def test_period_bounds_quarter_half_year_financial_year():
    from app.services.inception_service import _period_bounds

    assert _period_bounds("year", "2025") == (date(2025, 1, 1), date(2025, 12, 31))
    assert _period_bounds("quarter", "2025-Q1") == (date(2025, 1, 1), date(2025, 3, 31))
    assert _period_bounds("quarter", "2025-Q4") == (date(2025, 10, 1), date(2025, 12, 31))
    assert _period_bounds("half_year", "2025-H1") == (date(2025, 1, 1), date(2025, 6, 30))
    assert _period_bounds("half_year", "2025-H2") == (date(2025, 7, 1), date(2025, 12, 31))
    assert _period_bounds("financial_year", "2025") == (date(2025, 4, 1), date(2026, 3, 31))


def test_period_bounds_rejects_garbage():
    from app.exceptions import InvalidPeriodError
    from app.services.inception_service import _period_bounds

    with pytest.raises(InvalidPeriodError):
        _period_bounds("quarter", "not-a-period")
    with pytest.raises(InvalidPeriodError):
        _period_bounds("quarter", "2025-Q9")


# ── repositories: query construction / canonicalization ──────────────────────

def test_get_canonical_instruments_excludes_ii_suffix():
    from app.repositories.instrument_repo import get_canonical_instruments

    captured = {}

    class _FakeScalars:
        def all(self):
            return []

    class _FakeResult:
        def scalars(self):
            return _FakeScalars()

    class _FakeSession:
        async def execute(self, stmt):
            captured["sql"] = str(stmt)
            return _FakeResult()

    asyncio.run(get_canonical_instruments(_FakeSession()))
    assert "is_active" in captured["sql"]
    assert "NOT" in captured["sql"] and "LIKE" in captured["sql"]


def test_inception_strategy_fetch_all_filters_by_user_id():
    from app.repositories.inception_strategy_repo import fetch_all_for_user

    captured = {}

    class _FakeScalars:
        def all(self):
            return []

    class _FakeResult:
        def scalars(self):
            return _FakeScalars()

    class _FakeSession:
        async def execute(self, stmt):
            captured["sql"] = str(stmt)
            return _FakeResult()

    asyncio.run(fetch_all_for_user(_FakeSession(), uuid.uuid4()))
    assert "user_id" in captured["sql"]
