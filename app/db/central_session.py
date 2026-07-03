from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

# Basic-tier Azure SQL (5 DTU) has very little concurrency headroom — a larger pool
# just queues requests at the database instead of helping, so it's kept deliberately
# small. pool_pre_ping guards against Azure SQL's idle-connection resets.
central_engine = create_async_engine(
    settings.sql_connection_url,
    pool_size=5,
    max_overflow=5,
    pool_timeout=30,
    pool_pre_ping=True,
    echo=not settings.is_production,
)

CentralSessionLocal = async_sessionmaker(bind=central_engine, expire_on_commit=False)


async def get_central_session() -> AsyncGenerator[AsyncSession, None]:
    async with CentralSessionLocal() as session:
        yield session
