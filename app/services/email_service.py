"""
Outbound email delivery, backed by Gmail SMTP. This is the only module that
talks to smtplib — routers/notifications.py calls send_email_async, nothing
else should import smtplib directly.
"""

import smtplib
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.exceptions import EmailDeliveryError


def send_email(to_email: str, subject: str, body: str) -> None:
    """Blocking send — call send_email_async from async routes instead so the
    event loop isn't stalled for the duration of the SMTP round-trip.

    Sets Date/Message-ID/a named From explicitly — smtplib doesn't add these
    on its own, and their absence is itself a spam-classifier signal on top
    of everything else that makes app-generated mail relayed through a
    personal Gmail account look automated.
    """
    message = EmailMessage()
    message["From"] = formataddr((settings.smtp_from_name, settings.smtp_from_address))
    message["To"] = to_email
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain="gmail.com")
    message.set_content(body)

    try:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        raise EmailDeliveryError(f"Couldn't send email: {exc}") from exc


async def send_email_async(to_email: str, subject: str, body: str) -> None:
    await run_in_threadpool(send_email, to_email, subject, body)
