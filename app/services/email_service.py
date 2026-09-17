"""
Outbound email delivery, backed by Gmail SMTP. This is the only module that
talks to smtplib — routers/notifications.py calls send_email_async, nothing
else should import smtplib directly.
"""

import re
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.exceptions import EmailDeliveryError

# Desktop client repo issue: "add capability to add multiple email
# [addresses], cap it to 20". Enforced here too (not just client-side) —
# resolve_email_recipients reads a per-user Settings row the client writes,
# and nothing stops that row being written some other way (a future admin
# tool, a raw PUT /settings/notification_email_recipients call) with more
# than 20 or garbage entries, so this is defense in depth, not a duplicate
# of the client's own validation.
MAX_RECIPIENTS = 20

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def resolve_email_recipients(raw_setting, account_email: str) -> list[str]:
    """Turn the raw "notification_email_recipients" Settings value (whatever
    JSON the client last PUT there — a list of strings when written by the
    current client, but this must degrade sanely for None/a stale shape/a
    hand-edited value too) into the final, capped, de-duplicated recipient
    list for one send — falling back to *account_email* alone when nothing
    usable is saved, so a user who's never touched this setting (or cleared
    it) still gets notified exactly like before this feature existed.
    """
    if not isinstance(raw_setting, list):
        return [account_email]
    seen: set[str] = set()
    out: list[str] = []
    for entry in raw_setting:
        if not isinstance(entry, str):
            continue
        email = entry.strip()
        if not email or not _EMAIL_RE.match(email):
            continue
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(email)
        if len(out) >= MAX_RECIPIENTS:
            break
    return out or [account_email]


def send_email(to_emails: list[str], subject: str, body: str) -> None:
    """Blocking send — call send_email_async from async routes instead so the
    event loop isn't stalled for the duration of the SMTP round-trip.

    ``to_emails`` — one or more recipients, delivered as a SINGLE SMTP
    message (one "To" header listing all of them, one connection/RCPT
    round-trip) rather than one message per recipient: looping a separate
    send per address here would mean up to MAX_RECIPIENTS individual SMTP
    round-trips (each a few seconds, per this app's own logged timings) for
    every single notification — both far slower and, worse, exactly the
    "many rapid near-identical messages from a personal Gmail account"
    pattern already confirmed to get silently spam-filtered by Gmail (see
    the desktop client repo's alert-email RCA this feature followed from).

    Sets Date/Message-ID/a named From explicitly — smtplib doesn't add these
    on its own, and their absence is itself a spam-classifier signal on top
    of everything else that makes app-generated mail relayed through a
    personal Gmail account look automated.
    """
    message = EmailMessage()
    message["From"] = formataddr((settings.smtp_from_name, settings.smtp_from_address))
    message["To"] = ", ".join(to_emails)
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


async def send_email_async(to_emails: list[str], subject: str, body: str) -> None:
    await run_in_threadpool(send_email, to_emails, subject, body)
