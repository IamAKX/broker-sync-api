from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
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

# Generous for a paginated report PDF (a few hundred KB typical) while
# still bounding request size against an accidental/malicious huge upload.
_MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024


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


@router.post("/email/send-report", status_code=204)
async def send_report_email(
    recipients: str = Form(..., description="';'-separated email addresses"),
    subject: str = Form(..., min_length=1, max_length=200),
    attachment: UploadFile = File(...),
    current_user: CurrentUser = Depends(get_current_user),
) -> None:
    """Emails a generated report PDF (desktop client's Reports feature) to
    an explicit, caller-supplied recipient list — a report's OWN recipients
    (see the client's services/report_recipients.py), not the account-level
    "notification_email_recipients" setting /email/send reads. Recipients
    still come only from the authenticated caller's own request, same trust
    boundary as /email/test above, just extended to more than one address
    and capped the same way (email_service.MAX_RECIPIENTS) so this can't be
    used as an open relay.
    """
    parsed = email_service.validate_recipients(recipients.split(";"))
    if not parsed:
        raise HTTPException(status_code=400, detail="No valid recipient email addresses given.")

    if attachment.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(status_code=400, detail="Attachment must be a PDF file.")

    data = await attachment.read()
    if not data:
        raise HTTPException(status_code=400, detail="Attachment is empty.")
    if len(data) > _MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=400, detail="Attachment is too large (max 15MB).")

    body = f'Your requested report "{subject}" is attached.'
    await email_service.send_email_with_attachment_async(
        parsed, subject, body, data, attachment.filename or "report.pdf"
    )
