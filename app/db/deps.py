from collections.abc import AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_current_user
from app.db.central_session import CentralSessionLocal
from app.db.tenant_session import build_tenant_sessionmaker


async def get_central_db() -> AsyncGenerator[AsyncSession, None]:
    async with CentralSessionLocal() as session:
        yield session


async def get_tenant_db(
    current_user: CurrentUser = Depends(get_current_user),
) -> AsyncGenerator[AsyncSession, None]:
    """Schema is taken only from the verified JWT (current_user.schema_name) —
    never from a client-supplied header or query param.
    """
    sessionmaker = build_tenant_sessionmaker(current_user.schema_name)
    async with sessionmaker() as session:
        yield session
