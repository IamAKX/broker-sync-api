"""Control-flow tests for app.services.tenant_seed_service — no real
Postgres in this test environment (see test_strategies_formula_settings.py's
own note), so these verify the gating/branching logic (empty vs non-empty,
no admin tenant found, admin schema == target schema) against a minimal
fake session rather than exercising real SQL. Same asyncio.run(...) pattern
test_inception.py already uses for its own async service functions.

The actual cross-schema SQL (INSERT ... SELECT preserving ids, sequence
resets, JSONB casts, user_id remap) was validated separately against the
real deployed Postgres instance using disposable throwaway schemas
(scripts/_dry_run_seed_test.py, run once by hand and discarded — not part
of this suite since it needs a live DB) before this shipped.
"""

import asyncio
import uuid

from app.services import tenant_seed_service as svc


class _FakeResult:
    def __init__(self, rows=None, rowcount=0):
        self._rows = rows or []
        self.rowcount = rowcount

    def first(self):
        return self._rows[0] if self._rows else None

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    """Enough of AsyncSession's surface for seed_tenant_from_admin: routes
    each execute()/scalar() call to a small set of canned responses keyed
    by a substring of the SQL text, so the test controls exactly what each
    branch sees without a real connection."""

    def __init__(self, admin_row=None, counts=None, select_rows=None):
        self.admin_row = admin_row          # (schema_name, user_id) or None
        self.counts = counts or {}          # substring -> int, checked in order
        self.select_rows = select_rows or {}  # substring -> list[dict]
        self.executed: list[str] = []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.executed.append(sql)
        if 'FROM "Tenant"' in sql:
            return _FakeResult([self.admin_row] if self.admin_row else [])
        for key, rows in self.select_rows.items():
            if key in sql and sql.strip().upper().startswith("SELECT"):
                return _FakeResult(rows)
        if sql.strip().upper().startswith("INSERT") and "SELECT * FROM" in sql:
            return _FakeResult(rowcount=1)
        return _FakeResult()

    async def scalar(self, stmt, params=None):
        sql = str(stmt)
        self.executed.append(sql)
        for key, value in self.counts.items():
            if key in sql:
                return value
        return 0


def test_no_admin_tenant_returns_empty_report():
    session = _FakeSession(admin_row=None)
    report = asyncio.run(svc.seed_tenant_from_admin(session, "some_dss", uuid.uuid4()))
    assert report == {}


def test_target_is_admin_schema_is_a_noop():
    admin_id = uuid.uuid4()
    session = _FakeSession(admin_row=("hari_dss", admin_id))
    report = asyncio.run(svc.seed_tenant_from_admin(session, "hari_dss", admin_id))
    assert report == {}


def test_group_a_skipped_when_target_stock_not_empty():
    session = _FakeSession(counts={'"Stock"': 5})
    report = asyncio.run(svc._seed_group_a(session, "hari_dss", "krishna_dss"))
    assert all(v == "skipped (target already has data)" for v in report.values())
    assert set(report.keys()) == set(svc.GROUP_A_TABLES)


def test_group_a_copies_and_resets_sequences_when_target_stock_empty():
    session = _FakeSession(counts={'"Stock"': 0})
    report = asyncio.run(svc._seed_group_a(session, "hari_dss", "krishna_dss"))
    assert all("copied" in v for v in report.values())
    # A setval() call for each serial table (Stock, Metric).
    setval_calls = [s for s in session.executed if "setval" in s]
    assert len(setval_calls) == len(svc._GROUP_A_SERIAL_TABLES)


def test_group_b_table_skipped_when_target_user_already_has_rows():
    session = _FakeSession(counts={'"SavedStrategy" WHERE user_id': 1})
    outcome = asyncio.run(svc._copy_strategy_shaped(
        session, "SavedStrategy", "hari_dss", uuid.uuid4(), "krishna_dss", uuid.uuid4(),
    ))
    assert outcome == "skipped (target already has data)"


def test_group_b_table_copies_with_fresh_id_and_remapped_user():
    admin_user = uuid.uuid4()
    target_user = uuid.uuid4()
    admin_row = {
        "name": "VAH Buy", "active": True, "category": "Daily",
        "columns": [{"name": "VAH Buy"}], "row_filter": [],
    }
    session = _FakeSession(
        counts={'"SavedStrategy" WHERE user_id': 0},
        select_rows={'"SavedStrategy" WHERE user_id': [admin_row]},
    )
    outcome = asyncio.run(svc._copy_strategy_shaped(
        session, "SavedStrategy", "hari_dss", admin_user, "akash_dss", target_user,
    ))
    assert outcome == "copied (1 rows)"
    insert_calls = [s for s in session.executed if s.strip().upper().startswith("INSERT")]
    assert len(insert_calls) == 1


def test_variable_shaped_table_nothing_to_copy_when_admin_has_none():
    session = _FakeSession(counts={'"FormulaVariable" WHERE user_id': 0}, select_rows={})
    outcome = asyncio.run(svc._copy_variable_shaped(
        session, "FormulaVariable", "hari_dss", uuid.uuid4(), "akash_dss", uuid.uuid4(),
    ))
    assert outcome == "nothing to copy"


def test_full_seed_combines_group_a_and_group_b_reports():
    admin_id = uuid.uuid4()
    session = _FakeSession(
        admin_row=("hari_dss", admin_id),
        counts={'"Stock"': 0},  # Group A copies; every Group B table also reads as empty (0)
    )
    report = asyncio.run(svc.seed_tenant_from_admin(session, "krishna_dss", uuid.uuid4()))
    assert set(svc.GROUP_A_TABLES).issubset(report.keys())
    assert set(svc.GROUP_B_TABLES).issubset(report.keys())
