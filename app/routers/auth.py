from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_current_user
from app.db.deps import get_central_db
from app.schemas.auth import (
    AccessTokenResponse,
    ChangePasswordRequest,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    SignupRequest,
    TokenResponse,
    UpdateProfileRequest,
    UserProfileResponse,
)
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=TokenResponse, status_code=201)
async def signup(payload: SignupRequest, session: AsyncSession = Depends(get_central_db)) -> TokenResponse:
    return await auth_service.signup(
        session, payload.name, payload.email, payload.phone_number, payload.password
    )


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, session: AsyncSession = Depends(get_central_db)) -> TokenResponse:
    return await auth_service.login(session, payload.email, payload.password)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(payload: RefreshRequest, session: AsyncSession = Depends(get_central_db)) -> TokenResponse:
    return await auth_service.refresh(session, payload.refresh_token)


@router.post("/logout", status_code=204)
async def logout(payload: LogoutRequest, session: AsyncSession = Depends(get_central_db)) -> None:
    await auth_service.logout(session, payload.refresh_token)


@router.get("/me", response_model=UserProfileResponse)
async def get_me(
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_central_db),
) -> UserProfileResponse:
    return await auth_service.get_profile(session, current_user.user_id)


@router.patch("/me", response_model=AccessTokenResponse)
async def update_me(
    payload: UpdateProfileRequest,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_central_db),
) -> AccessTokenResponse:
    return await auth_service.update_profile(
        session, current_user.user_id, payload.name, payload.email, payload.phone_number
    )


@router.post("/change-password", status_code=204)
async def change_password(
    payload: ChangePasswordRequest,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_central_db),
) -> None:
    await auth_service.change_password(
        session, current_user.user_id, payload.current_password, payload.new_password
    )
