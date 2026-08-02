import asyncio
import smtplib
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError


def test_notifications_routes_registered():
    from app.main import create_app

    app = create_app()
    paths = {r.path for r in app.routes if hasattr(r, "path")}
    assert "/notifications/email/send" in paths
    assert "/notifications/email/test" in paths


def test_test_email_request_rejects_invalid_email():
    from app.schemas.notifications import TestEmailRequest

    with pytest.raises(ValidationError):
        TestEmailRequest(to_email="not-an-email")


def test_test_email_request_defaults():
    from app.schemas.notifications import TestEmailRequest

    req = TestEmailRequest(to_email="someone@example.com")
    assert req.subject == "Test Notification"
    assert req.message


def test_send_email_request_rejects_empty_message():
    from app.schemas.notifications import SendEmailRequest

    with pytest.raises(ValidationError):
        SendEmailRequest(subject="Hello", message="")


# ── email_service ────────────────────────────────────────────────────────────

def _fake_smtp():
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    smtp.__exit__.return_value = False
    return smtp


def test_send_email_logs_in_and_sends_expected_message():
    from app.services import email_service

    fake_smtp = _fake_smtp()
    with patch("smtplib.SMTP_SSL", return_value=fake_smtp) as mock_ssl:
        email_service.send_email("someone@example.com", "Subject", "Body text")

    mock_ssl.assert_called_once()
    fake_smtp.login.assert_called_once()
    sent_message = fake_smtp.send_message.call_args[0][0]
    assert sent_message["To"] == "someone@example.com"
    assert sent_message["Subject"] == "Subject"
    assert sent_message.get_content().strip() == "Body text"
    # Date/Message-ID/a named From aren't added by smtplib on its own — their
    # absence is itself a spam signal, so email_service sets them explicitly.
    assert sent_message["Date"]
    assert sent_message["Message-ID"]
    assert "<" in sent_message["From"] and "@" in sent_message["From"]


def test_send_email_wraps_smtp_exceptions():
    from app.exceptions import EmailDeliveryError
    from app.services import email_service

    with patch("smtplib.SMTP_SSL", side_effect=smtplib.SMTPAuthenticationError(535, b"bad creds")):
        with pytest.raises(EmailDeliveryError):
            email_service.send_email("someone@example.com", "Subject", "Body")


def test_send_email_wraps_connection_errors():
    from app.exceptions import EmailDeliveryError
    from app.services import email_service

    with patch("smtplib.SMTP_SSL", side_effect=OSError("connection refused")):
        with pytest.raises(EmailDeliveryError):
            email_service.send_email("someone@example.com", "Subject", "Body")


def test_send_email_async_delegates_to_sync_send():
    from app.services import email_service

    fake_smtp = _fake_smtp()
    with patch("smtplib.SMTP_SSL", return_value=fake_smtp):
        asyncio.run(email_service.send_email_async("someone@example.com", "Subject", "Body"))

    fake_smtp.send_message.assert_called_once()
