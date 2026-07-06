import re
import uuid
from collections.abc import AsyncIterator

from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import TenantBase
from app.exceptions import SchemaProvisioningError
from app.models.central import Tenant

_SCHEMA_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,60}$")
# Postgres SQLSTATE 42P06: "schema already exists" — schema names share a namespace
# with other schemas in the database, so a duplicate CREATE SCHEMA raises this. Verify
# against a real Postgres instance before relying on it in production; if the code
# differs, update this constant.
_PG_SCHEMA_ALREADY_EXISTS = "42P06"
_MAX_NAME_CANDIDATES = 1000


def _slugify_first_name(name: str) -> str:
    first_word = name.strip().split()[0] if name.strip() else "tenant"
    slug = re.sub(r"[^a-z0-9]", "", first_word.lower())
    return slug or "tenant"


def generate_schema_name_candidate(name: str, attempt: int) -> str:
    slug = _slugify_first_name(name)
    suffix = str(attempt) if attempt > 0 else ""
    schema_name = f"{slug}{suffix}_dss"
    if not _SCHEMA_NAME_RE.match(schema_name):
        raise SchemaProvisioningError(f"Generated schema name '{schema_name}' is not a valid identifier")
    return schema_name


async def next_candidate_name(central_session: AsyncSession, name: str) -> AsyncIterator[str]:
    """Checks the central Tenant table first so the common case (no collision) needs
    zero CREATE SCHEMA round-trips — CREATE SCHEMA itself stays authoritative for the
    rare case of a schema existing physically with no matching Tenant row.
    """
    result = await central_session.execute(text('SELECT schema_name FROM "Tenant"'))
    taken = {row[0] for row in result.all()}

    for attempt in range(_MAX_NAME_CANDIDATES):
        candidate = generate_schema_name_candidate(name, attempt)
        if candidate not in taken:
            yield candidate


def _create_schema_and_tables_sync(connection: Connection, schema_name: str) -> None:
    """Runs inside AsyncConnection.run_sync — `connection` is the *same* underlying
    DBAPI connection/transaction as the caller's AsyncSession, so schema creation,
    table creation, and the Tenant/User inserts made by the caller are all part of one
    atomic transaction: if anything fails, the whole thing rolls back together and no
    partial schema/tenant/user state is left behind.
    """
    connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
    scoped_connection = connection.execution_options(schema_translate_map={None: schema_name})
    TenantBase.metadata.create_all(scoped_connection)


async def provision_tenant(central_session: AsyncSession, name: str) -> Tenant:
    """Picks an unused schema name and creates the schema + tenant tables on the
    caller's existing connection/transaction (via `run_sync`), then stages (via
    `session.add`, not flushed) a Tenant row. `Tenant.id` is a client-side default
    (uuid4), so it's available on the returned object without a flush — callers add
    both the Tenant and User rows in one `session.begin()` block; the whole operation
    (schema + tables + Tenant + User) commits or rolls back together.
    """
    connection = await central_session.connection()

    schema_name: str | None = None
    async for candidate in next_candidate_name(central_session, name):
        try:
            await connection.run_sync(_create_schema_and_tables_sync, candidate)
        except DBAPIError as exc:
            if _PG_SCHEMA_ALREADY_EXISTS in str(exc.orig):
                continue
            raise SchemaProvisioningError(f"Failed to provision tenant schema: {exc}") from exc
        schema_name = candidate
        break

    if schema_name is None:
        raise SchemaProvisioningError(f"Exhausted schema name candidates for '{name}'")

    return Tenant(id=uuid.uuid4(), name=name, schema_name=schema_name)
