import uuid

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.config import settings
from app.db.central_session import CentralSessionLocal
from app.main import app

# Tests run against a real dev Azure SQL database (per project convention — no
# mocked DB layer). Each test gets its own tenant schema via a random first-name-like
# fixture; every tenant schema created during the session is tracked via the central
# Tenant table and dropped in the autouse teardown below.


@pytest.fixture(scope="session", autouse=True)
def _apply_central_migrations():
    config = Config("alembic_central.ini")
    config.set_main_option("sqlalchemy.url", settings.sql_sync_connection_url)
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
    # SQL Server refuses DROP SCHEMA while it still contains objects, so tables
    # are dropped first.
    for table in ("DailyStockValue", "Metric", "Stock"):
        await session.execute(
            text(f"IF OBJECT_ID('[{schema_name}].[{table}]', 'U') IS NOT NULL DROP TABLE [{schema_name}].[{table}]")
        )
    await session.execute(text(f"DROP SCHEMA IF EXISTS [{schema_name}]"))


@pytest.fixture(autouse=True)
async def _track_and_drop_tenant_schemas():
    yield
    async with CentralSessionLocal() as session:
        result = await session.execute(text("SELECT schema_name FROM [Tenant]"))
        schema_names = [row[0] for row in result.all()]
        for schema_name in schema_names:
            await _drop_tenant_schema(session, schema_name)
        await session.execute(text("DELETE FROM [RefreshToken]"))
        await session.execute(text("DELETE FROM [User]"))
        await session.execute(text("DELETE FROM [Tenant]"))
        await session.commit()
