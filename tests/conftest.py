import uuid

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.db.central_session import CentralSessionLocal
from app.main import app

# Tests run against a real local PostgreSQL database (per project convention — no
# mocked DB layer). Each test gets its own tenant schema via a random first-name-like
# fixture; every tenant schema created during the session is tracked via the central
# Tenant table and dropped in the autouse teardown below.


@pytest.fixture(scope="session", autouse=True)
def _apply_central_migrations():
    # migrations/central/env.py builds its own connection URL directly from
    # Settings (bypassing Alembic's ConfigParser, which chokes on "%" in a
    # percent-encoded password) — no need to set sqlalchemy.url here.
    config = Config("alembic_central.ini")
    command.upgrade(config, "head")
    yield


@pytest.fixture()
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture()
def unique_email() -> str:
    return f"test-{uuid.uuid4().hex[:12]}@example.com"


@pytest.fixture()
def unique_name() -> str:
    return f"Sundar{uuid.uuid4().hex[:8]}"


async def _drop_tenant_schema(session, schema_name: str) -> None:
    # Postgres DROP SCHEMA ... CASCADE removes the schema and everything in it in one
    # statement — no need to drop tables individually first (that was a SQL Server
    # workaround for its "can't drop a non-empty schema" restriction).
    await session.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))


@pytest.fixture(autouse=True)
async def _track_and_drop_tenant_schemas():
    yield
    async with CentralSessionLocal() as session:
        result = await session.execute(text('SELECT schema_name FROM "Tenant"'))
        schema_names = [row[0] for row in result.all()]
        for schema_name in schema_names:
            await _drop_tenant_schema(session, schema_name)
        await session.execute(text('DELETE FROM "RefreshToken"'))
        await session.execute(text('DELETE FROM "User"'))
        await session.execute(text('DELETE FROM "Tenant"'))
        await session.commit()
