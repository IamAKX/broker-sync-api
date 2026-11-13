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


# ── /chat/translate ──────────────────────────────────────────────────────

def _fake_converse(seen, text="**RELIANCE** 1,250.5 இல் முடிந்தது.", **reply):
    from app.services.chat.llm_client import ModelReply

    async def converse(**kwargs):
        seen.update(kwargs)
        return ModelReply(text=text, **reply)
    return converse


def test_translate_returns_the_model_translation(client, monkeypatch):
    from app.services.chat import translate as translate_service

    monkeypatch.setattr(chat_router.settings, "chat_enabled", True)
    seen = {}
    monkeypatch.setattr(translate_service.llm_client, "converse", _fake_converse(seen))
    r = client.post("/chat/translate", json={"text": "**RELIANCE** closed at 1,250.5.", "target": "ta"})
    assert r.status_code == 200
    assert r.json() == {"text": "**RELIANCE** 1,250.5 இல் முடிந்தது.", "target": "ta"}
    assert "Tamil script" in seen["system"] and "ticker symbols" in seen["system"]
    # the answer travels as data, not as an instruction
    assert seen["messages"][0]["content"][0]["text"] == "<text>\n**RELIANCE** closed at 1,250.5.\n</text>"


def test_translate_tanglish_is_the_tamil_translation_in_english_letters(client, monkeypatch):
    from app.services.chat import translate as translate_service

    monkeypatch.setattr(chat_router.settings, "chat_enabled", True)
    seen = {}
    monkeypatch.setattr(translate_service.llm_client, "converse", _fake_converse(
        seen, text="**RELIANCE** தற்போது 1,250.50 ல் உள்ளது.\n\n| பங்கு | விலை |\n|---|---|\n| TCS | 3,500.5 |",
    ))
    r = client.post("/chat/translate", json={"text": "x", "target": "ta-Latn"})
    assert r.json() == {
        "text": "**RELIANCE** tharpodhu 1,250.50 l ulladhu.\n\n| pangu | vilai |\n|---|---|\n| TCS | 3,500.5 |",
        "target": "ta-Latn",
    }
    assert "Tamil script" in seen["system"]          # same translation as "ta", then converted by rule


def test_tanglish_spelling_rules():
    from app.services.chat.tanglish import to_tanglish

    assert to_tanglish("12-Nov-2026 அன்று மிகவும் விழுந்த பங்குகள்:") == "12-Nov-2026 andru migavum vizhundha pangugal:"
    assert to_tanglish("வர்த்தகம் செய்யப்படுகிறது") == "vartthagam seyyappadugiradhu"
    assert to_tanglish("வணக்கம் நன்றி தமிழ்") == "vanakkam nandri thamizh"
    assert to_tanglish("காட்சி முயற்சி மஞ்சள் மற்ற சந்தை") == "kaatchi muyarchi manjal matra sandhai"
    assert to_tanglish("BDL (-5.21%) ஐ விட இரண்டு மடங்கு அதிகம்") == "BDL (-5.21%) ai vida irandu madangu adhigam"
    assert to_tanglish("no Tamil here: 52WH 1,608.80") == "no Tamil here: 52WH 1,608.80"
    assert not any("\u0b80" <= ch <= "\u0bff" for ch in to_tanglish("ஃ௧ தமிழ்ௐ"))   # nothing Tamil is left behind


def test_translate_strips_echoed_tags(client, monkeypatch):
    from app.services.chat import translate as translate_service

    monkeypatch.setattr(chat_router.settings, "chat_enabled", True)
    monkeypatch.setattr(translate_service.llm_client, "converse", _fake_converse({}, text="<text>\nவணக்கம்\n</text>"))
    assert client.post("/chat/translate", json={"text": "Hello"}).json()["text"] == "வணக்கம்"


def test_translate_is_503_when_disabled_and_rejects_other_languages(client, monkeypatch):
    monkeypatch.setattr(chat_router.settings, "chat_enabled", False)
    r = client.post("/chat/translate", json={"text": "hi", "target": "ta"})
    assert r.status_code == 503 and r.json()["code"] == "chat_disabled"
    monkeypatch.setattr(chat_router.settings, "chat_enabled", True)
    assert client.post("/chat/translate", json={"text": "hi", "target": "fr"}).status_code == 422


def test_translate_failures_are_a_clean_502(client, monkeypatch):
    from app.services.chat import translate as translate_service
    from app.services.chat.llm_client import LlmError

    monkeypatch.setattr(chat_router.settings, "chat_enabled", True)

    async def down(**kwargs):
        raise LlmError("The AI service is busy right now. Please try again in a moment.")
    monkeypatch.setattr(translate_service.llm_client, "converse", down)
    r = client.post("/chat/translate", json={"text": "hi"})
    assert r.status_code == 502 and r.json()["code"] == "chat_translate_failed"
    assert "busy" in r.json()["detail"]

    monkeypatch.setattr(translate_service.llm_client, "converse", _fake_converse({}, stop_reason="max_tokens"))
    assert client.post("/chat/translate", json={"text": "hi"}).status_code == 502
    monkeypatch.setattr(translate_service.llm_client, "converse", _fake_converse({}, text="", blocked=True))
    assert client.post("/chat/translate", json={"text": "hi"}).status_code == 502
