import pytest
from sqlalchemy.exc import ProgrammingError

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

    # The failing table's savepoint was rolled back before the error propagated.
    assert conn.nested_log == ["rollback"]
