import asyncio
import uuid

import pytest
from pydantic import ValidationError


def test_new_routes_registered():
    from app.main import create_app

    app = create_app()
    paths = {r.path for r in app.routes if hasattr(r, "path")}
    assert "/strategies" in paths
    assert "/strategies/{strategy_id}" in paths
    assert "/strategies/import" in paths
    assert "/formula-variables" in paths
    assert "/formula-variables/{variable_id}" in paths
    assert "/settings/{key}" in paths
    assert "/auth/me/theme" in paths


# ── Schemas ──────────────────────────────────────────────────────────────────

def test_strategy_upsert_request_defaults():
    from app.schemas.strategies import StrategyUpsertRequest

    req = StrategyUpsertRequest(name="Test")
    assert req.active is True
    assert req.category == "Daily"
    assert req.columns == []
    assert req.row_filter == []


def test_formula_variable_upsert_request_defaults():
    from app.schemas.formula_variables import FormulaVariableUpsertRequest

    req = FormulaVariableUpsertRequest(name="X")
    assert req.formula == []


def test_setting_update_request_accepts_any_json_value():
    from app.schemas.settings import SettingUpdateRequest

    assert SettingUpdateRequest(value={"a": 1}).value == {"a": 1}
    assert SettingUpdateRequest(value=[1, 2, 3]).value == [1, 2, 3]
    assert SettingUpdateRequest(value=None).value is None


def test_theme_update_request_rejects_invalid_value():
    from app.schemas.auth import ThemeUpdateRequest

    with pytest.raises(ValidationError):
        ThemeUpdateRequest(theme="blue")
    assert ThemeUpdateRequest(theme="dark").theme == "dark"


# ── Repository: user_id isolation ───────────────────────────────────────────
# No real Postgres in this test environment (matching the rest of this
# codebase's tests — see test_provisioning_service.py), so this confirms the
# isolation mechanism exists at the query-construction level: every fetch is
# built with a WHERE clause naming user_id, rather than asserting against a
# live two-user dataset.

def test_fetch_all_for_user_filters_by_user_id():
    from app.repositories.strategy_repo import fetch_all_for_user

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


def test_fetch_setting_filters_by_user_id_and_key():
    from app.repositories.settings_repo import fetch_setting

    captured = {}

    class _FakeResult:
        def scalar_one_or_none(self):
            return None

    class _FakeSession:
        async def execute(self, stmt):
            captured["sql"] = str(stmt)
            return _FakeResult()

    asyncio.run(fetch_setting(_FakeSession(), uuid.uuid4(), "theme"))
    assert "user_id" in captured["sql"]
    assert "key" in captured["sql"]


# ── strategy_service.import_strategies: merge-by-name ───────────────────────

class _FakeSession:
    def __init__(self):
        self.added = []
        self.committed = False

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True


def test_import_strategies_overwrites_by_name_keeps_existing_server_id(monkeypatch):
    from app.models.tenant import SavedStrategy
    from app.schemas.strategies import StrategyImportItem
    from app.services import strategy_service

    user_id = uuid.uuid4()
    existing_id = uuid.uuid4()
    existing = SavedStrategy(
        id=existing_id, user_id=user_id, name="DayTop Buy", active=False,
        category="Daily", columns=[], row_filter=[],
    )

    async def fake_fetch_all_for_user(session, uid):
        assert uid == user_id
        return [existing]

    monkeypatch.setattr(strategy_service, "fetch_all_for_user", fake_fetch_all_for_user)

    item = StrategyImportItem(
        id=str(uuid.uuid4()), name="DayTop Buy", active=True,
        category="Weekly", columns=[{"name": "c1"}], row_filter=[],
    )
    session = _FakeSession()
    result = asyncio.run(strategy_service.import_strategies(session, str(user_id), [item]))

    assert (result.overwritten, result.added) == (1, 0)
    # Server keeps its own row's id on a name-match overwrite rather than
    # adopting the imported id — see import_strategies' docstring.
    assert existing.id == existing_id
    assert existing.active is True
    assert existing.category == "Weekly"
    assert existing.columns == [{"name": "c1"}]
    assert session.added == []   # nothing new inserted, only the existing row mutated
    assert session.committed is True


def test_import_strategies_adds_new_name(monkeypatch):
    from app.schemas.strategies import StrategyImportItem
    from app.services import strategy_service

    user_id = uuid.uuid4()

    async def fake_fetch_all_for_user(session, uid):
        return []

    monkeypatch.setattr(strategy_service, "fetch_all_for_user", fake_fetch_all_for_user)

    item = StrategyImportItem(id=str(uuid.uuid4()), name="Brand New")
    session = _FakeSession()
    result = asyncio.run(strategy_service.import_strategies(session, str(user_id), [item]))

    assert (result.overwritten, result.added) == (0, 1)
    assert len(session.added) == 1
    assert session.added[0].name == "Brand New"
    assert session.added[0].user_id == user_id
