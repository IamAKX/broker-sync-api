from fastapi import APIRouter, Depends

from app.core.deps import CurrentUser, get_current_user
from app.schemas.notifications import SendEmailRequest, TestEmailRequest
from app.services import email_service

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.post("/email/send", status_code=204)
async def send_notification_email(
    payload: SendEmailRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> None:
    """Always delivers to the caller's own registered email — the recipient
    comes from the verified access token, never from the request body, so a
    caller can't use this to relay mail to an arbitrary address."""
    await email_service.send_email_async(current_user.email, payload.subject, payload.message)


@router.post("/email/test", status_code=204)
async def send_test_email(
    payload: TestEmailRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> None:
    """Unlike /email/send, the recipient is caller-supplied — this exists so a
    logged-in user can verify deliverability against any inbox they choose."""
    await email_service.send_email_async(payload.to_email, payload.subject, payload.message)
