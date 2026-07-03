from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.deps import get_central_db
from app.schemas.auth import LoginRequest, LogoutRequest, RefreshRequest, SignupRequest, TokenResponse
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=TokenResponse, status_code=201)
async def signup(payload: SignupRequest, session: AsyncSession = Depends(get_central_db)) -> TokenResponse:
    return await auth_service.signup(session, payload.name, payload.email, payload.password)


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, session: AsyncSession = Depends(get_central_db)) -> TokenResponse:
    return await auth_service.login(session, payload.email, payload.password)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(payload: RefreshRequest, session: AsyncSession = Depends(get_central_db)) -> TokenResponse:
    return await auth_service.refresh(session, payload.refresh_token)


@router.post("/logout", status_code=204)
async def logout(payload: LogoutRequest, session: AsyncSession = Depends(get_central_db)) -> None:
    await auth_service.logout(session, payload.refresh_token)
