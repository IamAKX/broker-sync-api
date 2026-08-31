from dataclasses import dataclass

import jwt
from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer

from app.core.security import decode_access_token
from app.exceptions import AdminOnlyError, InvalidCredentialsError

_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)

# The one account allowed to trigger Admin Controls > Inception Sync (see
# app.routers.inception's sync-lmv-metrics endpoint) — a single named
# account by explicit request, not a role (this tenant, "hari_dss", is
# already this exact user's own schema, so gating the endpoint here also
# fixes which tenant schema get_tenant_db resolves to for that request —
# see require_admin_email's own docstring). Matched case-insensitively
# since email identity shouldn't hinge on casing.
_ADMIN_EMAIL = "sundarhari10@gmail.com"


@dataclass(frozen=True)
class CurrentUser:
    user_id: str
    tenant_id: str
    schema_name: str
    role: str
    name: str
    email: str
    phone_number: str


async def get_current_user(token: str | None = Depends(_oauth2_scheme)) -> CurrentUser:
    if token is None:
        raise InvalidCredentialsError("Missing bearer token")
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError as exc:
        raise InvalidCredentialsError("Invalid or expired token") from exc

    return CurrentUser(
        user_id=payload["sub"],
        tenant_id=payload["tenant_id"],
        schema_name=payload["schema_name"],
        role=payload["role"],
        name=payload["name"],
        email=payload["email"],
        phone_number=payload["phone_number"],
    )


def require_role(required_role: str):
    async def _check(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current_user.role != required_role:
            raise InvalidCredentialsError(f"Requires role '{required_role}'")
        return current_user

    return _check


async def require_admin_email(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Restricts an endpoint to _ADMIN_EMAIL specifically. Combined with
    app.db.deps.get_tenant_db (which resolves its schema from THIS SAME
    verified current_user.schema_name, never a client-supplied value) an
    endpoint depending on both this and get_tenant_db is guaranteed to
    read/write that one admin's own tenant schema ("hari_dss") and no
    other — there's no separate "which schema" parameter to pass or get
    wrong."""
    if current_user.email.strip().lower() != _ADMIN_EMAIL:
        raise AdminOnlyError("This action is restricted to the Inception admin account")
    return current_user
