import asyncio
import uuid
from datetime import date, timedelta

import pytest


# ── Wiring: routes, models ───────────────────────────────────────────────────

def test_inception_routes_registered():
    from app.main import create_app

    app = create_app()
    paths = {r.path for r in app.routes if hasattr(r, "path")}
    for expected in [
        "/inception/availability", "/inception/instruments", "/inception/bars",
        "/inception/strategies", "/inception/strategies/{strategy_id}",
        "/inception/formula-variables", "/inception/formula-variables/{variable_id}",
    ]:
        assert expected in paths
    # Group A/B computation (and every endpoint that served it) moved
    # entirely to the desktop client — see that repo's
    # services/inception_formula_engine.py — so these no longer exist here.
    for removed in ["/inception/snapshot", "/inception/hmv", "/inception/recompute", "/inception/columns"]:
        assert removed not in paths


def test_inception_models_registered_on_tenant_base():
    import sys
    for module_name in ["app.models", "app.models.central", "app.models.tenant", "app.db.base"]:
        sys.modules.pop(module_name, None)
    import app.models  # noqa: F401
    from app.db.base import TenantBase

    for table_name in ("InceptionStrategy", "InceptionFormulaVariable"):
        assert table_name in TenantBase.metadata.tables
    # Group A/B are computed and stored entirely client-side now — this
    # table (and the server-side computation that wrote it) is gone.
    assert "InceptionDerivedValue" not in TenantBase.metadata.tables


def test_central_models_include_instrument_eodbar_tradingcalendar():
    from app.db.base import CentralBase

    for table_name in ("Instrument", "EodBar", "TradingCalendar"):
        assert table_name in CentralBase.metadata.tables


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


def test_get_bars_in_range_filters_by_date_and_optional_symbols():
    from app.repositories.instrument_repo import get_bars_in_range

    captured = {}

    class _FakeMappings:
        def all(self):
            return []

    class _FakeResult:
        def mappings(self):
            return _FakeMappings()

    class _FakeSession:
        async def execute(self, stmt):
            captured["sql"] = str(stmt)
            return _FakeResult()

    asyncio.run(get_bars_in_range(_FakeSession(), date(2025, 1, 1), date(2025, 1, 31)))
    assert "trade_date" in captured["sql"]
    no_filter_sql = captured["sql"]

    asyncio.run(get_bars_in_range(_FakeSession(), date(2025, 1, 1), date(2025, 1, 31), symbols=["ABB_I"]))
    assert "symbol IN" in captured["sql"] and "symbol IN" not in no_filter_sql


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


# ── services.inception_service.get_bars: range validation ───────────────────

def test_get_bars_rejects_inverted_range():
    from app.exceptions import InvalidDateRangeError
    from app.services.inception_service import get_bars

    with pytest.raises(InvalidDateRangeError):
        asyncio.run(get_bars(None, date(2025, 2, 1), date(2025, 1, 1)))


def test_get_bars_rejects_oversized_range():
    from app.exceptions import InvalidDateRangeError
    from app.services.inception_service import _MAX_BARS_RANGE_DAYS, get_bars

    with pytest.raises(InvalidDateRangeError):
        asyncio.run(get_bars(None, date(2000, 1, 1), date(2000, 1, 1) + timedelta(days=_MAX_BARS_RANGE_DAYS + 1)))
