from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

# RDS db.t3.micro's max_connections default (~66) is generous relative to a single
# dev-phase EC2 instance, but the pool is still kept modest — a larger pool just queues
# requests at the database instead of helping. pool_pre_ping guards against RDS's
# idle-connection resets.
central_engine = create_async_engine(
    settings.sql_connection_url,
    connect_args={
        "ssl": settings.sql_ssl_mode,
    },
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
