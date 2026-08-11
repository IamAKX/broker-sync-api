import asyncio
import uuid
from datetime import datetime

import pytest
from pydantic import ValidationError


def test_tenant_metadata_registers_strategy_signal_model():
    for module_name in [
        "app.models", "app.models.central", "app.models.tenant",
        "app.db.base", "app.services.provisioning_service",
    ]:
        import sys
        sys.modules.pop(module_name, None)

    import app.models  # noqa: F401

    from app.db.base import TenantBase

    assert "StrategySignal" in TenantBase.metadata.tables
    columns = {c.name for c in TenantBase.metadata.tables["StrategySignal"].columns}
    assert columns == {
        "id", "user_id", "strategy_id", "strategy_name", "symbol", "sector",
        "direction", "status", "entry_time", "entry_price", "resolved_at",
        "running_high", "running_low", "score", "risk_reward", "metrics",
        "event_time", "created_at", "updated_at",
    }


# ── Real HTTP round trip for page_size ───────────────────────────────────
# Regression coverage for a bug that reached production: every other test in
# this file calls repo/service functions directly with native Python types,
# which never exercises FastAPI's actual query-STRING parsing. A bare
# `Literal[25, 50, 100]` (int members) query-param annotation does NOT
# coerce the incoming string "25" into the int literal 25 in this FastAPI/
# Pydantic version — it 422s on every value, including objectively valid
# ones (confirmed live: the client sent page_size=25 exactly, and the
# server still rejected it). Fixed by accepting a plain `int` at the FastAPI
# layer and validating against the allowed set manually in the service
# layer instead (see strategy_signal_service.list_signals). These tests go
# through a real TestClient HTTP request specifically so this class of bug
# can't silently reappear.

def _test_app_with_fake_auth():
    import uuid as _uuid
    from fastapi import FastAPI
    from app.core.deps import CurrentUser, get_current_user
    from app.db.deps import get_tenant_db
    from app.exceptions import register_exception_handlers
    from app.routers.strategy_signals import router

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=str(_uuid.uuid4()), tenant_id=str(_uuid.uuid4()), schema_name="test_dss",
        role="owner", name="Test", email="test@example.com", phone_number="0000000000",
    )
    app.dependency_overrides[get_tenant_db] = lambda: None
    return app


def test_list_signals_accepts_every_allowed_page_size_over_real_http(monkeypatch):
    from fastapi.testclient import TestClient
    from app.schemas.strategy_signals import StrategySignalListResponse
    from app.services import strategy_signal_service

    async def fake_list_signals(session, user_id, **kwargs):
        return StrategySignalListResponse(
            items=[], total=0, page=kwargs.get("page", 1),
            page_size=kwargs.get("page_size", 25), total_pages=0,
        )

    monkeypatch.setattr(strategy_signal_service, "list_signals", fake_list_signals)

    client = TestClient(_test_app_with_fake_auth())
    for size in (25, 50, 100):
        response = client.get("/strategy-signals", params={"page": 1, "page_size": size})
        assert response.status_code == 200, response.json()
        assert response.json()["page_size"] == size


def test_list_signals_rejects_invalid_page_size_over_real_http():
    from fastapi.testclient import TestClient

    client = TestClient(_test_app_with_fake_auth())
    response = client.get("/strategy-signals", params={"page_size": 30})

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_page_size"


def test_strategy_signal_routes_registered():
    from app.main import create_app

    app = create_app()
    paths = {r.path for r in app.routes if hasattr(r, "path")}
    assert "/strategy-signals" in paths
    assert "/strategy-signals/{signal_id}" in paths


# ── Schemas ──────────────────────────────────────────────────────────────────

def test_upsert_request_requires_valid_direction():
    from app.schemas.strategy_signals import StrategySignalUpsertRequest

    with pytest.raises(ValidationError):
        StrategySignalUpsertRequest(
            strategy_id=str(uuid.uuid4()), strategy_name="S", symbol="INFY",
            direction="LONG", status="open",
        )


def test_upsert_request_requires_valid_status():
    from app.schemas.strategy_signals import StrategySignalUpsertRequest

    with pytest.raises(ValidationError):
        StrategySignalUpsertRequest(
            strategy_id=str(uuid.uuid4()), strategy_name="S", symbol="INFY",
            direction="BUY", status="pending",   # never synced — see model docstring
        )


def test_upsert_request_accepts_open_signal_minimal_fields():
    from app.schemas.strategy_signals import StrategySignalUpsertRequest

    req = StrategySignalUpsertRequest(
        strategy_id=str(uuid.uuid4()), strategy_name="Breakout", symbol="INFY",
        direction="BUY", status="open",
    )
    assert req.metrics == {}
    assert req.sector is None


# ── Repository: user_id isolation + combined AND filtering ──────────────────
# No real Postgres in this test environment (matching test_strategies_
# formula_settings.py's rationale) — confirms filter/isolation composition at
# the query-construction level.

def test_fetch_page_for_user_filters_by_user_id():
    from app.repositories.strategy_signal_repo import fetch_page_for_user

    captured = []

    class _FakeScalars:
        def all(self):
            return []

    class _FakeCountResult:
        def scalar_one(self):
            return 0

    class _FakeResult:
        def scalars(self):
            return _FakeScalars()

    class _FakeSession:
        async def execute(self, stmt):
            captured.append(str(stmt))
            return _FakeCountResult() if len(captured) == 1 else _FakeResult()

    asyncio.run(fetch_page_for_user(_FakeSession(), uuid.uuid4()))
    assert all("user_id" in sql for sql in captured)


def test_fetch_page_for_user_combines_every_filter_with_and():
    from app.repositories.strategy_signal_repo import fetch_page_for_user

    captured = []

    class _FakeScalars:
        def all(self):
            return []

    class _FakeCountResult:
        def scalar_one(self):
            return 0

    class _FakeResult:
        def scalars(self):
            return _FakeScalars()

    class _FakeSession:
        async def execute(self, stmt):
            captured.append(str(stmt))
            return _FakeCountResult() if len(captured) == 1 else _FakeResult()

    asyncio.run(fetch_page_for_user(
        _FakeSession(), uuid.uuid4(),
        strategy_id=uuid.uuid4(), direction="BUY", symbol="INFY", sector="IT",
        status="open", start_time=datetime(2026, 8, 11, 10, 0),
        end_time=datetime(2026, 8, 11, 13, 0),
    ))
    list_sql = captured[-1]
    for expected in ("strategy_id", "direction", "symbol", "sector", "status", "event_time"):
        assert expected in list_sql, f"{expected!r} missing from combined filter query"
    # AND-combined (not OR) — every WHERE clause in the same statement.
    assert list_sql.count("WHERE") == 1


def test_delete_all_for_user_filters_by_user_id():
    from app.repositories.strategy_signal_repo import delete_all_for_user

    captured = {}

    class _FakeResult:
        rowcount = 3

    class _FakeSession:
        async def execute(self, stmt):
            captured["sql"] = str(stmt)
            return _FakeResult()

    deleted = asyncio.run(delete_all_for_user(_FakeSession(), uuid.uuid4()))
    assert "user_id" in captured["sql"]
    assert deleted == 3


# ── Service: upsert event_time derivation + list/clear plumbing ─────────────

class _FakeSession:
    def __init__(self):
        self.added = []
        self.committed = False

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True


def test_upsert_signal_derives_event_time_from_resolved_at_when_present(monkeypatch):
    from app.schemas.strategy_signals import StrategySignalUpsertRequest
    from app.services import strategy_signal_service

    captured = {}

    async def fake_upsert_for_user(session, user_id, signal_id, **fields):
        captured.update(fields)
        class _Row:
            id = signal_id
            strategy_id = fields["strategy_id"]
            strategy_name = fields["strategy_name"]
            symbol = fields["symbol"]
            sector = fields["sector"]
            direction = fields["direction"]
            status = fields["status"]
            entry_time = fields["entry_time"]
            entry_price = fields["entry_price"]
            resolved_at = fields["resolved_at"]
            running_high = fields["running_high"]
            running_low = fields["running_low"]
            score = fields["score"]
            risk_reward = fields["risk_reward"]
            metrics = fields["metrics"]
            event_time = fields["event_time"]
        return _Row()

    monkeypatch.setattr(strategy_signal_service, "upsert_for_user", fake_upsert_for_user)

    entry_time = datetime(2026, 8, 11, 10, 5)
    resolved_at = datetime(2026, 8, 11, 10, 40)
    payload = StrategySignalUpsertRequest(
        strategy_id=str(uuid.uuid4()), strategy_name="Breakout", symbol="INFY",
        direction="BUY", status="stopped_out",
        entry_time=entry_time, resolved_at=resolved_at,
    )
    session = _FakeSession()
    result = asyncio.run(
        strategy_signal_service.upsert_signal(session, str(uuid.uuid4()), str(uuid.uuid4()), payload)
    )

    assert captured["event_time"] == resolved_at   # resolved_at wins over entry_time
    assert result.status == "stopped_out"
    assert session.committed is True


def test_upsert_signal_falls_back_to_entry_time_when_still_open(monkeypatch):
    from app.schemas.strategy_signals import StrategySignalUpsertRequest
    from app.services import strategy_signal_service

    captured = {}

    async def fake_upsert_for_user(session, user_id, signal_id, **fields):
        captured.update(fields)
        class _Row:
            id = signal_id
            strategy_id = fields["strategy_id"]
            strategy_name = fields["strategy_name"]
            symbol = fields["symbol"]
            sector = fields["sector"]
            direction = fields["direction"]
            status = fields["status"]
            entry_time = fields["entry_time"]
            entry_price = fields["entry_price"]
            resolved_at = fields["resolved_at"]
            running_high = fields["running_high"]
            running_low = fields["running_low"]
            score = fields["score"]
            risk_reward = fields["risk_reward"]
            metrics = fields["metrics"]
            event_time = fields["event_time"]
        return _Row()

    monkeypatch.setattr(strategy_signal_service, "upsert_for_user", fake_upsert_for_user)

    entry_time = datetime(2026, 8, 11, 10, 5)
    payload = StrategySignalUpsertRequest(
        strategy_id=str(uuid.uuid4()), strategy_name="Breakout", symbol="INFY",
        direction="BUY", status="open", entry_time=entry_time,
    )
    session = _FakeSession()
    asyncio.run(
        strategy_signal_service.upsert_signal(session, str(uuid.uuid4()), str(uuid.uuid4()), payload)
    )

    assert captured["event_time"] == entry_time


def test_list_signals_rejects_invalid_page_size_at_service_layer():
    from app.exceptions import InvalidPageSizeError
    from app.services import strategy_signal_service

    with pytest.raises(InvalidPageSizeError):
        asyncio.run(
            strategy_signal_service.list_signals(_FakeSession(), str(uuid.uuid4()), page_size=30)
        )


def test_list_signals_computes_total_pages(monkeypatch):
    from app.services import strategy_signal_service

    async def fake_fetch_page_for_user(session, user_id, **kwargs):
        assert kwargs["page_size"] == 25
        return [], 51   # 51 rows at page_size 25 -> 3 pages

    monkeypatch.setattr(strategy_signal_service, "fetch_page_for_user", fake_fetch_page_for_user)

    result = asyncio.run(
        strategy_signal_service.list_signals(_FakeSession(), str(uuid.uuid4()), page=1, page_size=25)
    )
    assert result.total == 51
    assert result.total_pages == 3


def test_list_signals_zero_results_zero_pages(monkeypatch):
    from app.services import strategy_signal_service

    async def fake_fetch_page_for_user(session, user_id, **kwargs):
        return [], 0

    monkeypatch.setattr(strategy_signal_service, "fetch_page_for_user", fake_fetch_page_for_user)

    result = asyncio.run(
        strategy_signal_service.list_signals(_FakeSession(), str(uuid.uuid4()))
    )
    assert result.total == 0
    assert result.total_pages == 0


def test_clear_signals_deletes_and_commits(monkeypatch):
    from app.services import strategy_signal_service

    calls = []

    async def fake_delete_all_for_user(session, user_id):
        calls.append(user_id)
        return 5

    monkeypatch.setattr(strategy_signal_service, "delete_all_for_user", fake_delete_all_for_user)

    session = _FakeSession()
    asyncio.run(strategy_signal_service.clear_signals(session, str(uuid.uuid4())))
    assert len(calls) == 1
    assert session.committed is True
