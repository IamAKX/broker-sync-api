"""Seeds a tenant schema with a starting copy of the admin tenant's data —
issue: every signup provisions a brand-new, completely empty schema (see
app.services.provisioning_service.provision_tenant), so a second/non-admin
login had none of the admin's strategies, formula variables, Formula
Builder settings, or Historic Upload archive, and Data & Settings > Sync
(which only pulls Inception's own OHLC bars) couldn't fix that — most of
LMV/HMV read blank for that account.

seed_tenant_from_admin() copies the admin ("app.core.deps.ADMIN_EMAIL")
tenant's data into another tenant's schema — called once from
auth_service.signup() right after a fresh tenant is provisioned, and reused
by scripts/backfill_tenant_seed.py for tenants that already existed before
this shipped.

Safety model — this NEVER overwrites or reads back from the admin's own
data based on anything the target tenant does afterward, and never touches
a table the target tenant already has rows in:

  * GROUP_A (Stock-keyed reference/archive data: Stock, Metric,
    HistoricalStockValue, OpeningRangeCapture, LmvDailySnapshot,
    LmvDailySnapshotWide) is copied as ONE unit, ids preserved as-is, and
    ONLY when the target's own Stock table is currently empty. Stock.id is
    a plain serial primary key that every other Group-A table's stock_id
    foreign key points at — copying verbatim is only correct when nothing
    in the target schema already uses those same ids for a *different*
    stock, which "Stock is empty" guarantees by construction (nothing else
    in Group A can hold rows without Stock rows for them to reference).
    Metric.id works the same way for HistoricalStockValue/LmvDailySnapshot's
    metric_id. After copying, both serial sequences are advanced past the
    copied max id so the target's own future inserts don't collide.

  * GROUP_B (SavedStrategy, FormulaVariable, UserSetting, InceptionStrategy,
    InceptionFormulaVariable) is private per user_id even within one
    schema (see those models' own docstrings) — copied table by table,
    each only when *target_user_id*'s own rows in that table are currently
    empty, with a freshly generated id (Python uuid4, not the admin's own
    id) and user_id rewritten to target_user_id. A genuine independent
    copy, not a shared/aliased row.

Both group copies are gated per table/tenant on "currently empty", so
calling this again on a tenant that already has some of its own data (or
was already seeded once) is a safe no-op for whatever it finds non-empty —
see the per-table strings in the returned report.

Runs on app.db.central_session.CentralSessionLocal (no schema_translate_map
bound) rather than a get_tenant_db-style scoped session, specifically
because it needs to reference TWO schemas (admin's and the target's) in
the same statement/transaction.
"""

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import ADMIN_EMAIL

GROUP_A_TABLES = [
    "Stock", "Metric", "HistoricalStockValue",
    "OpeningRangeCapture", "LmvDailySnapshot", "LmvDailySnapshotWide",
]
# (table, has_serial_id) — only Stock/Metric own a serial primary key that
# needs its sequence advanced after a verbatim id-preserving copy.
_GROUP_A_SERIAL_TABLES = ["Stock", "Metric"]

# "Strategy-shaped": id, user_id, name, active, category, columns, row_filter,
# created_at, updated_at.
_STRATEGY_SHAPED_TABLES = ["SavedStrategy", "InceptionStrategy"]
# "Variable-shaped": id, user_id, name, formula, created_at, updated_at.
_VARIABLE_SHAPED_TABLES = ["FormulaVariable", "InceptionFormulaVariable"]
GROUP_B_TABLES = _STRATEGY_SHAPED_TABLES + _VARIABLE_SHAPED_TABLES + ["UserSetting"]


async def _find_admin_tenant(session: AsyncSession) -> tuple[str, uuid.UUID] | None:
    """(schema_name, user_id) for ADMIN_EMAIL, or None if that account
    doesn't exist yet — e.g. a fresh dev/test database that's never seen a
    real signup. Never raises for that case; there's simply nothing to seed
    from."""
    result = await session.execute(
        text(
            'SELECT t.schema_name, u.id FROM "Tenant" t '
            'JOIN "User" u ON u.tenant_id = t.id '
            "WHERE lower(u.email) = :email"
        ),
        {"email": ADMIN_EMAIL},
    )
    row = result.first()
    return (row[0], row[1]) if row is not None else None


async def _seed_group_a(session: AsyncSession, admin_schema: str, target_schema: str) -> dict[str, str]:
    stock_count = await session.scalar(text(f'SELECT COUNT(*) FROM "{target_schema}"."Stock"'))
    if stock_count:
        return {t: "skipped (target already has data)" for t in GROUP_A_TABLES}

    report: dict[str, str] = {}
    for table in GROUP_A_TABLES:
        result = await session.execute(text(
            f'INSERT INTO "{target_schema}"."{table}" SELECT * FROM "{admin_schema}"."{table}"'
        ))
        report[table] = f"copied ({result.rowcount} rows)"

    for table in _GROUP_A_SERIAL_TABLES:
        await session.execute(text(
            f"SELECT setval(pg_get_serial_sequence('\"{target_schema}\".\"{table}\"', 'id'), "
            f'GREATEST((SELECT COALESCE(MAX("id"), 0) FROM "{target_schema}"."{table}"), 1))'
        ))
    return report


async def _copy_strategy_shaped(
    session: AsyncSession, table: str, admin_schema: str, admin_user_id: uuid.UUID,
    target_schema: str, target_user_id: uuid.UUID,
) -> str:
    existing = await session.scalar(text(
        f'SELECT COUNT(*) FROM "{target_schema}"."{table}" WHERE user_id = :uid'
    ), {"uid": target_user_id})
    if existing:
        return "skipped (target already has data)"

    rows = (await session.execute(text(
        f'SELECT name, active, category, columns, row_filter '
        f'FROM "{admin_schema}"."{table}" WHERE user_id = :uid'
    ), {"uid": admin_user_id})).mappings().all()
    if not rows:
        return "nothing to copy"

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for row in rows:
        await session.execute(text(
            f'INSERT INTO "{target_schema}"."{table}" '
            f"(id, user_id, name, active, category, columns, row_filter, created_at, updated_at) "
            f"VALUES (:id, :user_id, :name, :active, :category, "
            f"CAST(:columns AS JSONB), CAST(:row_filter AS JSONB), :created_at, :updated_at)"
        ), {
            "id": uuid.uuid4(), "user_id": target_user_id,
            "name": row["name"], "active": row["active"], "category": row["category"],
            "columns": json.dumps(row["columns"]), "row_filter": json.dumps(row["row_filter"]),
            "created_at": now, "updated_at": now,
        })
    return f"copied ({len(rows)} rows)"


async def _copy_variable_shaped(
    session: AsyncSession, table: str, admin_schema: str, admin_user_id: uuid.UUID,
    target_schema: str, target_user_id: uuid.UUID,
) -> str:
    existing = await session.scalar(text(
        f'SELECT COUNT(*) FROM "{target_schema}"."{table}" WHERE user_id = :uid'
    ), {"uid": target_user_id})
    if existing:
        return "skipped (target already has data)"

    rows = (await session.execute(text(
        f'SELECT name, formula FROM "{admin_schema}"."{table}" WHERE user_id = :uid'
    ), {"uid": admin_user_id})).mappings().all()
    if not rows:
        return "nothing to copy"

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for row in rows:
        await session.execute(text(
            f'INSERT INTO "{target_schema}"."{table}" (id, user_id, name, formula, created_at, updated_at) '
            f"VALUES (:id, :user_id, :name, CAST(:formula AS JSONB), :created_at, :updated_at)"
        ), {
            "id": uuid.uuid4(), "user_id": target_user_id,
            "name": row["name"], "formula": json.dumps(row["formula"]),
            "created_at": now, "updated_at": now,
        })
    return f"copied ({len(rows)} rows)"


async def _copy_user_setting(
    session: AsyncSession, admin_schema: str, admin_user_id: uuid.UUID,
    target_schema: str, target_user_id: uuid.UUID,
) -> str:
    existing = await session.scalar(text(
        f'SELECT COUNT(*) FROM "{target_schema}"."UserSetting" WHERE user_id = :uid'
    ), {"uid": target_user_id})
    if existing:
        return "skipped (target already has data)"

    rows = (await session.execute(text(
        f'SELECT key, value FROM "{admin_schema}"."UserSetting" WHERE user_id = :uid'
    ), {"uid": admin_user_id})).mappings().all()
    if not rows:
        return "nothing to copy"

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for row in rows:
        await session.execute(text(
            f'INSERT INTO "{target_schema}"."UserSetting" (user_id, key, value, updated_at) '
            f"VALUES (:user_id, :key, CAST(:value AS JSONB), :updated_at)"
        ), {
            "user_id": target_user_id, "key": row["key"],
            "value": json.dumps(row["value"]), "updated_at": now,
        })
    return f"copied ({len(rows)} rows)"


async def _seed_group_b(
    session: AsyncSession, admin_schema: str, admin_user_id: uuid.UUID,
    target_schema: str, target_user_id: uuid.UUID,
) -> dict[str, str]:
    report: dict[str, str] = {}
    for table in _STRATEGY_SHAPED_TABLES:
        report[table] = await _copy_strategy_shaped(
            session, table, admin_schema, admin_user_id, target_schema, target_user_id,
        )
    for table in _VARIABLE_SHAPED_TABLES:
        report[table] = await _copy_variable_shaped(
            session, table, admin_schema, admin_user_id, target_schema, target_user_id,
        )
    report["UserSetting"] = await _copy_user_setting(
        session, admin_schema, admin_user_id, target_schema, target_user_id,
    )
    return report


async def seed_tenant_from_admin(
    session: AsyncSession, target_schema: str, target_user_id: uuid.UUID,
) -> dict[str, str]:
    """Best-effort starter-data copy — see module docstring for the full
    safety model. Returns {table_name: outcome_string}; an empty dict means
    there was nothing to do (no admin tenant yet, or target IS the admin
    tenant). Callers decide what "best-effort" means for them — this
    function itself does not catch/swallow errors, so a caller that wants
    a signup or migration step to survive a seeding failure must wrap the
    call itself (see auth_service.signup / scripts/backfill_tenant_seed.py).
    """
    admin = await _find_admin_tenant(session)
    if admin is None:
        return {}
    admin_schema, admin_user_id = admin
    if admin_schema == target_schema:
        return {}

    report = await _seed_group_a(session, admin_schema, target_schema)
    report.update(await _seed_group_b(
        session, admin_schema, admin_user_id, target_schema, target_user_id,
    ))
    return report
