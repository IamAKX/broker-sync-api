from pydantic import BaseModel, EmailStr, Field


class SendEmailRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=10_000)


class TestEmailRequest(BaseModel):
    to_email: EmailStr
    subject: str = Field(default="Test Notification", min_length=1, max_length=200)
    message: str = Field(
        default="This is a test notification from Broker Sync.",
        min_length=1,
        max_length=10_000,
    )
