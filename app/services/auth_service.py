import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    refresh_token_expiry,
    verify_password,
)
from app.exceptions import DuplicateEmailError, InvalidCredentialsError, TenantNotFoundError, UserNotFoundError
from app.models.central import RefreshToken, Tenant, User
from app.schemas.auth import AccessTokenResponse, TokenResponse, UserProfileResponse
from app.services.provisioning_service import provision_tenant


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _issue_tokens(session: AsyncSession, user: User, tenant: Tenant) -> TokenResponse:
    access_token = create_access_token(
        sub=str(user.id),
        tenant_id=str(tenant.id),
        schema_name=tenant.schema_name,
        role=user.role,
        name=user.name,
        email=user.email,
        phone_number=user.phone_number,
    )
    refresh_token = generate_refresh_token()
    session.add(
        RefreshToken(
            id=uuid.uuid4(),
            user_id=user.id,
            token_hash=hash_refresh_token(refresh_token),
            expires_at=refresh_token_expiry(),
        )
    )
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


async def signup(
    session: AsyncSession, name: str, email: str, phone_number: str, password: str
) -> TokenResponse:
    existing = await session.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none() is not None:
        raise DuplicateEmailError("Email already registered")

    async with session.begin_nested():
        # The SELECT above already autobegan the session's outer transaction, so we
        # open a SAVEPOINT here instead of a new top-level transaction. Schema creation,
        # tenant tables, and the Tenant/User inserts below all still share one overall
        # transaction — if anything fails, everything rolls back together
        # (BACKEND_ARCHITECTURE.md §2.4), no orphaned schema or partial rows possible.
        tenant = await provision_tenant(session, name)
        session.add(tenant)

        user = User(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            name=name,
            email=email,
            phone_number=phone_number,
            password_hash=hash_password(password),
            role="owner",
            current_login_at=_now(),
        )
        session.add(user)
        await session.flush()

        tokens = await _issue_tokens(session, user, tenant)

    # begin_nested() only manages the SAVEPOINT above — unlike the begin() context
    # manager it replaces, it does not commit the outer (autobegun) transaction on
    # exit, so an explicit commit is required here, matching login()/refresh() below.
    await session.commit()

    return tokens


async def login(session: AsyncSession, email: str, password: str) -> TokenResponse:
    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        raise InvalidCredentialsError("Invalid email or password")

    tenant = await session.get(Tenant, user.tenant_id)
    if tenant is None:
        raise TenantNotFoundError("Tenant not found for user")

    user.last_login_at = user.current_login_at
    user.current_login_at = _now()

    tokens = await _issue_tokens(session, user, tenant)
    await session.commit()
    return tokens


async def refresh(session: AsyncSession, refresh_token: str) -> TokenResponse:
    token_hash = hash_refresh_token(refresh_token)
    result = await session.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    stored = result.scalar_one_or_none()

    if stored is None or stored.revoked_at is not None or stored.expires_at < datetime.now(timezone.utc).replace(tzinfo=None):
        raise InvalidCredentialsError("Invalid or expired refresh token")

    user = await session.get(User, stored.user_id)
    if user is None:
        raise InvalidCredentialsError("Invalid or expired refresh token")
    tenant = await session.get(Tenant, user.tenant_id)
    if tenant is None:
        raise TenantNotFoundError("Tenant not found for user")

    stored.revoked_at = datetime.now(timezone.utc).replace(tzinfo=None)
    tokens = await _issue_tokens(session, user, tenant)
    await session.commit()
    return tokens


async def logout(session: AsyncSession, refresh_token: str) -> None:
    token_hash = hash_refresh_token(refresh_token)
    result = await session.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    stored = result.scalar_one_or_none()
    if stored is not None and stored.revoked_at is None:
        stored.revoked_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await session.commit()


async def get_profile(session: AsyncSession, user_id: str) -> UserProfileResponse:
    user = await session.get(User, uuid.UUID(user_id))
    if user is None:
        raise UserNotFoundError("User not found")
    return UserProfileResponse(
        name=user.name,
        email=user.email,
        phone_number=user.phone_number,
        role=user.role,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


async def update_profile(
    session: AsyncSession, user_id: str, name: str, email: str, phone_number: str
) -> AccessTokenResponse:
    user = await session.get(User, uuid.UUID(user_id))
    if user is None:
        raise UserNotFoundError("User not found")

    if email != user.email:
        existing = await session.execute(select(User).where(User.email == email))
        if existing.scalar_one_or_none() is not None:
            raise DuplicateEmailError("Email already registered")

    user.name = name
    user.email = email
    user.phone_number = phone_number
    await session.flush()

    tenant = await session.get(Tenant, user.tenant_id)
    if tenant is None:
        raise TenantNotFoundError("Tenant not found for user")

    # name/email/phone_number are also JWT claims, so the caller's current access
    # token is now stale — issue a fresh one reflecting the update. No new refresh
    # token needed since the session itself hasn't changed, only its claims.
    access_token = create_access_token(
        sub=str(user.id),
        tenant_id=str(tenant.id),
        schema_name=tenant.schema_name,
        role=user.role,
        name=user.name,
        email=user.email,
        phone_number=user.phone_number,
    )
    await session.commit()
    return AccessTokenResponse(access_token=access_token)


async def change_password(
    session: AsyncSession, user_id: str, current_password: str, new_password: str
) -> None:
    user = await session.get(User, uuid.UUID(user_id))
    if user is None:
        raise UserNotFoundError("User not found")
    if not verify_password(current_password, user.password_hash):
        raise InvalidCredentialsError("Current password is incorrect")

    user.password_hash = hash_password(new_password)
    await session.commit()
