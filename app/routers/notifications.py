from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_current_user
from app.db.deps import get_tenant_db
from app.schemas.notifications import SendEmailRequest, TestEmailRequest
from app.services import email_service, settings_service

router = APIRouter(prefix="/notifications", tags=["notifications"])

# Same key the desktop client's services/email_recipients_config.py reads/
# writes via the generic PUT /settings/{key} — this is the one place that
# name has to match, since nothing else enforces it structurally.
_RECIPIENTS_SETTING_KEY = "notification_email_recipients"


@router.post("/email/send", status_code=204)
async def send_notification_email(
    payload: SendEmailRequest,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> None:
    """Delivers to every address in the caller's own saved
    "notification_email_recipients" setting (capped at
    email_service.MAX_RECIPIENTS, falling back to just the caller's own
    registered email if that setting is empty/unset/unusable — see
    email_service.resolve_email_recipients), as ONE message to all of them.

    The recipient list still comes ONLY from server-side account state the
    caller configured ahead of time via PUT /settings/{key}, never from
    THIS request's body — a caller can't use this endpoint itself to relay
    mail to an arbitrary one-off address by putting it in the payload; the
    only way to add a recipient is to durably register it on your own
    account first, same trust boundary as before this feature existed."""
    setting = await settings_service.get_setting(session, current_user.user_id, _RECIPIENTS_SETTING_KEY)
    recipients = email_service.resolve_email_recipients(setting.value, current_user.email)
    await email_service.send_email_async(recipients, payload.subject, payload.message)


@router.post("/email/test", status_code=204)
async def send_test_email(
    payload: TestEmailRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> None:
    """Unlike /email/send, the recipient is caller-supplied — this exists so a
    logged-in user can verify deliverability against any inbox they choose."""
    await email_service.send_email_async([payload.to_email], payload.subject, payload.message)
