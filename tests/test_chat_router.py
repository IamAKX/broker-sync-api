from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from app.core.deps import CurrentUser, get_current_user
from app.main import app
from app.routers import chat as chat_router
from app.services.chat import rate_limit

USER = CurrentUser("00000000-0000-0000-0000-000000000001", "t", "akash_dss", "user", "A", "a@b.c", "1")


@pytest.fixture
def client(monkeypatch):
    rate_limit.reset()
    app.dependency_overrides[get_current_user] = lambda: USER

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    async def noop(*a, **k):
        return None

    monkeypatch.setattr(chat_router, "build_tenant_sessionmaker", lambda schema: (lambda: _Session()))
    monkeypatch.setattr(chat_router, "ensure_tenant_schema_tables", noop)
    yield TestClient(app)
    app.dependency_overrides.clear()
    rate_limit.reset()


def test_status_reports_config(client, monkeypatch):
    monkeypatch.setattr(chat_router.settings, "chat_enabled", True)
    body = client.get("/chat/status").json()
    assert body["enabled"] is True and body["suggested_questions"]


def test_ask_is_503_when_disabled(client, monkeypatch):
    monkeypatch.setattr(chat_router.settings, "chat_enabled", False)
    r = client.post("/chat/ask", json={"question": "hi"})
    assert r.status_code == 503 and r.json()["code"] == "chat_disabled"


def test_ask_streams_sse_events(client, monkeypatch):
    monkeypatch.setattr(chat_router.settings, "chat_enabled", True)
    seen = {}

    async def fake_run(session, user, question, history, live=None):
        seen["args"] = (user.user_id, question, history)
        seen["live"] = live
        yield "stage", {"stage": "understanding"}
        yield "token", {"text": "héllo"}
        yield "done", {"kind": "sql"}

    monkeypatch.setattr(chat_router, "run_chat", fake_run)
    r = client.post(
        "/chat/ask",
        json={"question": "  q  ", "history": [{"role": "user", "text": "a"}]},
        headers={"Accept-Encoding": "gzip"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers.get("content-encoding") == "identity"   # keeps GZipMiddleware from buffering the stream
    assert 'event: token\ndata: {"text": "h\\u00e9llo"}\n\n' in r.text
    assert r.text.rstrip().endswith('"kind": "sql"}')
    assert seen["args"] == (USER.user_id, "q", [{"role": "user", "text": "a"}])
    assert seen["live"] is None                                # older clients send no live block


def test_ask_passes_the_live_lmv_snapshot_through(client, monkeypatch):
    monkeypatch.setattr(chat_router.settings, "chat_enabled", True)
    seen = {}

    async def fake_run(session, user, question, history, live=None):
        seen.setdefault("live", []).append(live)
        yield "done", {"kind": "live"}

    monkeypatch.setattr(chat_router, "run_chat", fake_run)
    row = {"symbol": "TCS", "display_name": "TCS", "sector": "IT", "metrics": {"Current": 3500.5, "% Change": -1.2}}
    r = client.post("/chat/ask", json={"question": "q", "live": {
        "running": True, "as_of": "2026-11-11 10:32:05", "warning": "feed stale",
        "columns": ["Current", "% Change", "CWH"],
        "rows": [["TCS", "TCS", "IT", 3500.5, -1.2, None], ["", "bad row", None, 1, 2, 3], ["short"]]}})
    assert r.status_code == 200
    live = seen["live"][0]
    assert live.usable and live.as_of == "2026-11-11 10:32:05" and live.warning == "feed stale"
    assert live.rows == (row,) and live.metric_keys() == ["% Change", "Current"]

    # Not running: the reason is kept, any rows sent by mistake are dropped.
    r = client.post("/chat/ask", json={"question": "q", "live": {
        "running": False, "reason": "Live Master View is not running.",
        "columns": ["Current"], "rows": [["TCS", "TCS", "IT", 1]]}})
    assert r.status_code == 200
    live = seen["live"][1]
    assert not live.usable and live.rows == () and live.reason == "Live Master View is not running."


def test_ask_rate_limited(client, monkeypatch):
    monkeypatch.setattr(chat_router.settings, "chat_enabled", True)
    monkeypatch.setattr(rate_limit.settings, "chat_rate_limit_per_minute", 1)

    async def fake_run(*a, **k):
        yield "done", {}

    monkeypatch.setattr(chat_router, "run_chat", fake_run)
    assert client.post("/chat/ask", json={"question": "a"}).status_code == 200
    r = client.post("/chat/ask", json={"question": "b"})
    assert r.status_code == 429 and r.json()["code"] == "chat_rate_limited"


def test_ask_validates_body(client, monkeypatch):
    monkeypatch.setattr(chat_router.settings, "chat_enabled", True)
    assert client.post("/chat/ask", json={"question": ""}).status_code == 422
    assert client.post("/chat/ask", json={"question": "x", "history": [{"role": "system", "text": "t"}]}).status_code == 422
