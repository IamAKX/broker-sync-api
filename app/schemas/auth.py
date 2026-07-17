from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

_PHONE_NUMBER_PATTERN = r"^\+?[0-9 \-()]{7,20}$"


class SignupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    phone_number: str = Field(min_length=7, max_length=20, pattern=_PHONE_NUMBER_PATTERN)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserProfileResponse(BaseModel):
    name: str
    email: str
    phone_number: str
    role: str
    created_at: datetime
    last_login_at: datetime | None


class UpdateProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    phone_number: str = Field(min_length=7, max_length=20, pattern=_PHONE_NUMBER_PATTERN)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)
