from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

# Same physical database as central_session's engine, kept as a separate engine so
# central and tenant-scoped connections never share a pool slot budget with each other
# under load.
tenant_engine = create_async_engine(
    settings.sql_connection_url,
    pool_size=5,
    max_overflow=5,
    pool_timeout=30,
    pool_pre_ping=True,
    echo=not settings.is_production,
)


def build_tenant_sessionmaker(schema_name: str) -> async_sessionmaker[AsyncSession]:
    """One set of ORM models is reused for every tenant: the session's bind is
    rebound to the caller's schema via schema_translate_map, translating the `None`
    (default) schema in table metadata to the tenant's actual schema at query time.
    """
    scoped_engine = tenant_engine.execution_options(schema_translate_map={None: schema_name})
    return async_sessionmaker(bind=scoped_engine, expire_on_commit=False)


async def get_tenant_session_for_schema(schema_name: str) -> AsyncGenerator[AsyncSession, None]:
    sessionmaker = build_tenant_sessionmaker(schema_name)
    async with sessionmaker() as session:
        yield session
