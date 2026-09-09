from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.engine_config import engine_connect_args, engine_pool_kwargs

# RDS db.t3.micro's max_connections default (~66) is generous relative to a single
# dev-phase EC2 instance, but the pool is still kept modest — a larger pool just queues
# requests at the database instead of helping. pool_pre_ping guards against RDS's
# idle-connection resets; the statement/idle timeouts in engine_connect_args stop a
# single stuck query from holding a pool slot long enough to starve every other
# request. All of it is tunable via the Settings db_* fields.
central_engine = create_async_engine(
    settings.sql_connection_url,
    connect_args=engine_connect_args("brokersync-central"),
    echo=not settings.is_production,
    **engine_pool_kwargs(),
)

CentralSessionLocal = async_sessionmaker(bind=central_engine, expire_on_commit=False)


async def get_central_session() -> AsyncGenerator[AsyncSession, None]:
    async with CentralSessionLocal() as session:
        yield session
