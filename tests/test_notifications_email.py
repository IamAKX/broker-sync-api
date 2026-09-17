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
        email_service.send_email(["someone@example.com"], "Subject", "Body text")

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


def test_send_email_delivers_to_every_recipient_as_one_message():
    """Multiple recipients — ONE SMTP message/round-trip listing all of
    them, not one send per address (see send_email's own docstring for why:
    looping individual sends is both slower and the exact spam-burst
    pattern already confirmed to get filtered by Gmail)."""
    from app.services import email_service

    fake_smtp = _fake_smtp()
    with patch("smtplib.SMTP_SSL", return_value=fake_smtp) as mock_ssl:
        email_service.send_email(["a@example.com", "b@example.com"], "Subject", "Body")

    mock_ssl.assert_called_once()
    fake_smtp.send_message.assert_called_once()
    sent_message = fake_smtp.send_message.call_args[0][0]
    assert sent_message["To"] == "a@example.com, b@example.com"


def test_send_email_wraps_smtp_exceptions():
    from app.exceptions import EmailDeliveryError
    from app.services import email_service

    with patch("smtplib.SMTP_SSL", side_effect=smtplib.SMTPAuthenticationError(535, b"bad creds")):
        with pytest.raises(EmailDeliveryError):
            email_service.send_email(["someone@example.com"], "Subject", "Body")


def test_send_email_wraps_connection_errors():
    from app.exceptions import EmailDeliveryError
    from app.services import email_service

    with patch("smtplib.SMTP_SSL", side_effect=OSError("connection refused")):
        with pytest.raises(EmailDeliveryError):
            email_service.send_email(["someone@example.com"], "Subject", "Body")


def test_send_email_async_delegates_to_sync_send():
    from app.services import email_service

    fake_smtp = _fake_smtp()
    with patch("smtplib.SMTP_SSL", return_value=fake_smtp):
        asyncio.run(email_service.send_email_async(["someone@example.com"], "Subject", "Body"))

    fake_smtp.send_message.assert_called_once()


# ── resolve_email_recipients (multi-recipient cap/validation) ──────────────

def test_resolve_email_recipients_falls_back_to_account_email_when_unset():
    from app.services import email_service

    assert email_service.resolve_email_recipients(None, "me@example.com") == ["me@example.com"]


def test_resolve_email_recipients_falls_back_when_not_a_list():
    """A hand-edited or stale setting value (e.g. still a bare string from
    before this feature) must never crash the send — degrade to the
    account email, same as unset."""
    from app.services import email_service

    assert email_service.resolve_email_recipients("me@example.com", "me@example.com") == ["me@example.com"]


def test_resolve_email_recipients_uses_the_saved_list():
    from app.services import email_service

    result = email_service.resolve_email_recipients(
        ["a@example.com", "b@example.com"], "me@example.com",
    )
    assert result == ["a@example.com", "b@example.com"]


def test_resolve_email_recipients_drops_malformed_entries_and_duplicates():
    from app.services import email_service

    result = email_service.resolve_email_recipients(
        ["a@example.com", "not-an-email", "A@EXAMPLE.com", "  ", 42], "me@example.com",
    )
    assert result == ["a@example.com"]


def test_resolve_email_recipients_caps_at_max_recipients():
    from app.services import email_service

    many = [f"user{i}@example.com" for i in range(email_service.MAX_RECIPIENTS + 5)]
    result = email_service.resolve_email_recipients(many, "me@example.com")
    assert len(result) == email_service.MAX_RECIPIENTS
    assert result == many[: email_service.MAX_RECIPIENTS]


def test_resolve_email_recipients_falls_back_when_list_has_nothing_usable():
    from app.services import email_service

    result = email_service.resolve_email_recipients(["not-an-email", ""], "me@example.com")
    assert result == ["me@example.com"]


# ── /notifications/email/send — real HTTP round trip ────────────────────────
# Same dependency-override + TestClient shape as test_strategy_signals.py's
# _test_app_with_fake_auth, exercising the actual router (not just the pure
# helper above) so the settings-lookup -> resolve -> send wiring itself is
# covered, not just resolve_email_recipients in isolation.

def _test_app_with_fake_auth(account_email="test@example.com"):
    import uuid as _uuid
    from fastapi import FastAPI
    from app.core.deps import CurrentUser, get_current_user
    from app.db.deps import get_tenant_db
    from app.exceptions import register_exception_handlers
    from app.routers.notifications import router

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=str(_uuid.uuid4()), tenant_id=str(_uuid.uuid4()), schema_name="test_dss",
        role="owner", name="Test", email=account_email, phone_number="0000000000",
    )
    app.dependency_overrides[get_tenant_db] = lambda: None
    return app


def test_send_notification_email_uses_saved_recipients(monkeypatch):
    from fastapi.testclient import TestClient
    from app.schemas.settings import SettingResponse
    from app.services import email_service, settings_service

    async def fake_get_setting(session, user_id, key):
        assert key == "notification_email_recipients"
        return SettingResponse(key=key, value=["a@example.com", "b@example.com"])

    sent = {}

    async def fake_send_email_async(to_emails, subject, message):
        sent["to_emails"] = to_emails

    monkeypatch.setattr(settings_service, "get_setting", fake_get_setting)
    monkeypatch.setattr(email_service, "send_email_async", fake_send_email_async)

    client = TestClient(_test_app_with_fake_auth())
    response = client.post("/notifications/email/send", json={"subject": "Hi", "message": "Body"})

    assert response.status_code == 204
    assert sent["to_emails"] == ["a@example.com", "b@example.com"]


def test_send_notification_email_falls_back_to_account_email_when_unset(monkeypatch):
    from fastapi.testclient import TestClient
    from app.schemas.settings import SettingResponse
    from app.services import email_service, settings_service

    async def fake_get_setting(session, user_id, key):
        return SettingResponse(key=key, value=None)

    sent = {}

    async def fake_send_email_async(to_emails, subject, message):
        sent["to_emails"] = to_emails

    monkeypatch.setattr(settings_service, "get_setting", fake_get_setting)
    monkeypatch.setattr(email_service, "send_email_async", fake_send_email_async)

    client = TestClient(_test_app_with_fake_auth(account_email="me@example.com"))
    response = client.post("/notifications/email/send", json={"subject": "Hi", "message": "Body"})

    assert response.status_code == 204
    assert sent["to_emails"] == ["me@example.com"]


def test_send_test_email_still_wraps_single_recipient_in_a_list(monkeypatch):
    """/email/test is unaffected by the multi-recipient feature — it's
    always a single caller-supplied address, unrelated to the account's
    saved recipient list."""
    from fastapi.testclient import TestClient
    from app.services import email_service

    sent = {}

    async def fake_send_email_async(to_emails, subject, message):
        sent["to_emails"] = to_emails

    monkeypatch.setattr(email_service, "send_email_async", fake_send_email_async)

    client = TestClient(_test_app_with_fake_auth())
    response = client.post("/notifications/email/test", json={"to_email": "someone@example.com"})

    assert response.status_code == 204
    assert sent["to_emails"] == ["someone@example.com"]
