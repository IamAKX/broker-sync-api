from dataclasses import dataclass

import jwt
from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer

from app.core.security import decode_access_token
from app.exceptions import InvalidCredentialsError

_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    user_id: str
    tenant_id: str
    schema_name: str
    role: str


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
    )


def require_role(required_role: str):
    async def _check(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current_user.role != required_role:
            raise InvalidCredentialsError(f"Requires role '{required_role}'")
        return current_user

    return _check
