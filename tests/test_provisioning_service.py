import asyncio

import pytest
from sqlalchemy.exc import IntegrityError, ProgrammingError

from app.db.base import TenantBase
from app.models import tenant as tenant_models  # noqa: F401


class _FakeNestedTransaction:
    def __init__(self, log):
        self._log = log

    def commit(self):
        self._log.append("commit")

    def rollback(self):
        self._log.append("rollback")


class _FakeConnection:
    """Enough of Connection's surface for _create_all_tables_individually:
    begin_nested() per call, nothing else touched directly (create_all is
    monkeypatched below rather than actually run against this fake)."""

    def __init__(self):
        self.nested_log = []

    def begin_nested(self):
        return _FakeNestedTransaction(self.nested_log)


def _already_exists_error():
    return ProgrammingError("CREATE TABLE ...", {}, Exception("relation already exists"))


def test_one_table_already_existing_does_not_block_later_tables(monkeypatch):
    """Regression test for the bug that left OpeningRangeCapture missing from
    an existing tenant schema: a batched create_all() aborts entirely on the
    first "already exists" error, silently skipping every table later in
    dependency order. Creating tables one at a time under a SAVEPOINT must
    isolate that failure to just the one table."""
    from app.services import provisioning_service

    tables = TenantBase.metadata.sorted_tables
    assert len(tables) >= 2, "need at least 2 tenant tables for this test to be meaningful"
    already_exists_table = tables[0].name

    attempted = []

    def fake_create_all(metadata_bind, tables=None, checkfirst=True):
        table = tables[0]
        attempted.append(table.name)
        if table.name == already_exists_table:
            raise _already_exists_error()

    monkeypatch.setattr(TenantBase.metadata, "create_all", fake_create_all)

    conn = _FakeConnection()
    provisioning_service._create_all_tables_individually(conn)

    # Every table was attempted — the "already exists" failure on the first
    # one didn't stop the loop from reaching the rest.
    assert attempted == [t.name for t in tables]
    # The failing table's savepoint was rolled back; every other table's was committed.
    assert conn.nested_log == [
        "rollback" if name == already_exists_table else "commit" for name in attempted
    ]


def _concurrent_create_race_error():
    # The real shape seen in production the first time a genuinely NEW table
    # (StrategySignal) was added to an already-provisioned schema: two
    # concurrent requests both saw the schema as unprovisioned and both
    # issued CREATE TABLE for it at once — Postgres's own catalog insert for
    # the table's row type serializes that, and the loser gets a
    # UniqueViolation on pg_type's name/namespace index (an IntegrityError),
    # NOT the DuplicateTable ProgrammingError a genuinely-already-existing
    # table raises.
    return IntegrityError(
        "CREATE TABLE ...", {},
        Exception(
            'duplicate key value violates unique constraint "pg_type_typname_nsp_index"\n'
            "DETAIL:  Key (typname, typnamespace)=(StrategySignal, 17391) already exists."
        ),
    )


def test_concurrent_create_table_race_does_not_block_later_tables(monkeypatch):
    """Regression test for the transient 500s seen on the first request burst
    after StrategySignal was deployed: this race must be treated the same as
    an ordinary "already exists" skip, not propagate and abort the request."""
    from app.services import provisioning_service

    tables = TenantBase.metadata.sorted_tables
    assert len(tables) >= 2, "need at least 2 tenant tables for this test to be meaningful"
    racing_table = tables[0].name

    attempted = []

    def fake_create_all(metadata_bind, tables=None, checkfirst=True):
        table = tables[0]
        attempted.append(table.name)
        if table.name == racing_table:
            raise _concurrent_create_race_error()

    monkeypatch.setattr(TenantBase.metadata, "create_all", fake_create_all)

    conn = _FakeConnection()
    provisioning_service._create_all_tables_individually(conn)   # must not raise

    assert attempted == [t.name for t in tables]
    assert conn.nested_log == [
        "rollback" if name == racing_table else "commit" for name in attempted
    ]


def test_non_already_exists_error_still_raises(monkeypatch):
    from app.services import provisioning_service

    tables = TenantBase.metadata.sorted_tables
    failing_table = tables[0].name

    def fake_create_all(metadata_bind, tables=None, checkfirst=True):
        table = tables[0]
        if table.name == failing_table:
            raise ProgrammingError("CREATE TABLE ...", {}, Exception("permission denied"))

    monkeypatch.setattr(TenantBase.metadata, "create_all", fake_create_all)

    conn = _FakeConnection()
    with pytest.raises(ProgrammingError, match="permission denied"):
        provisioning_service._create_all_tables_individually(conn)


def test_unrelated_integrity_error_still_raises(monkeypatch):
    """The widened except (ProgrammingError, IntegrityError) must not swallow
    a genuinely unrelated IntegrityError — only the specific concurrent-create
    race signature (see _BENIGN_CREATE_TABLE_RACE_MARKERS) is benign."""
    from app.services import provisioning_service

    tables = TenantBase.metadata.sorted_tables
    failing_table = tables[0].name

    def fake_create_all(metadata_bind, tables=None, checkfirst=True):
        table = tables[0]
        if table.name == failing_table:
            raise IntegrityError("CREATE TABLE ...", {}, Exception("not null constraint violated"))

    monkeypatch.setattr(TenantBase.metadata, "create_all", fake_create_all)

    conn = _FakeConnection()
    with pytest.raises(IntegrityError, match="not null constraint violated"):
        provisioning_service._create_all_tables_individually(conn)

    # The failing table's savepoint was rolled back before the error propagated.
    assert conn.nested_log == ["rollback"]


class _FakeAsyncConnection:
    def __init__(self, log):
        self._log = log

    async def run_sync(self, fn, *args):
        self._log.append(("run_sync", args))


class _FakeAsyncSession:
    """Stand-in for the AsyncSession ensure_tenant_schema_tables receives —
    tracks call order so tests can assert the DDL is committed before (not
    instead of) _provisioned_schemas gets marked done."""

    def __init__(self, log, commit_raises=None):
        self._log = log
        self._commit_raises = commit_raises

    async def connection(self):
        return _FakeAsyncConnection(self._log)

    async def commit(self):
        self._log.append(("commit", ()))
        if self._commit_raises is not None:
            raise self._commit_raises


def test_ensure_tenant_schema_tables_commits_before_marking_provisioned(monkeypatch):
    """Regression test for the actual root cause behind OpeningRangeCapture
    staying missing: this function's DDL was never committed for read-only
    requests (the majority of traffic never calls session.commit() itself),
    so it was silently rolled back on session close — while _provisioned_schemas
    still marked the schema done, permanently skipping retries."""
    from app.services import provisioning_service

    monkeypatch.setattr(provisioning_service, "_provisioned_schemas", set())
    log = []
    session = _FakeAsyncSession(log)

    asyncio.run(provisioning_service.ensure_tenant_schema_tables(session, "hari_dss"))

    assert [event for event, _ in log] == ["run_sync", "commit"]
    assert "hari_dss" in provisioning_service._provisioned_schemas


def test_ensure_tenant_schema_tables_skips_when_already_provisioned(monkeypatch):
    from app.services import provisioning_service

    monkeypatch.setattr(provisioning_service, "_provisioned_schemas", {"hari_dss"})
    log = []
    session = _FakeAsyncSession(log)

    asyncio.run(provisioning_service.ensure_tenant_schema_tables(session, "hari_dss"))

    assert log == []


def test_ensure_tenant_schema_tables_does_not_mark_provisioned_if_commit_fails(monkeypatch):
    from app.services import provisioning_service

    monkeypatch.setattr(provisioning_service, "_provisioned_schemas", set())
    log = []
    session = _FakeAsyncSession(log, commit_raises=RuntimeError("commit failed"))

    with pytest.raises(RuntimeError, match="commit failed"):
        asyncio.run(provisioning_service.ensure_tenant_schema_tables(session, "hari_dss"))

    assert "hari_dss" not in provisioning_service._provisioned_schemas
