"""Tests for the "Fetch from Equal Solution" feature: app/services/
eqldata_client.py (vendor HTTP wrapper), app/repositories/instrument_repo.py's
new vendor-ingest functions, and app/services/inception_vendor_sync_service.py
(the orchestration). No real network or database — matches this test suite's
existing convention (see tests/test_inception.py) of fake session doubles
and monkeypatched HTTP calls.
"""
import asyncio
import io
import zipfile
from datetime import date

import pytest


# ── router wiring ────────────────────────────────────────────────────────

def test_vendor_sync_route_registered():
    from app.main import create_app

    app = create_app()
    routes = {r.path: r.methods for r in app.routes if hasattr(r, "path")}
    assert "/inception/vendor-sync" in routes
    assert "POST" in routes["/inception/vendor-sync"]


# ── app.services.eqldata_client ──────────────────────────────────────────

class _FakeResponse:
    def __init__(self, status_code=200, json_body=None, content=b"", text=""):
        self.status_code = status_code
        self._json_body = json_body
        self.content = content
        self.text = text

    def json(self):
        if self._json_body is None:
            raise ValueError("no json body")
        return self._json_body


def test_generate_auth_token_returns_token_on_200(monkeypatch):
    from app.services import eqldata_client

    monkeypatch.setattr(
        eqldata_client.requests, "post",
        lambda *a, **kw: _FakeResponse(200, {"token": "abc123"}),
    )
    token = eqldata_client.generate_auth_token("e@x.com", "pw", "https://api.equalsolution.net")
    assert token == "abc123"


def test_generate_auth_token_raises_on_non_200(monkeypatch):
    from app.exceptions import VendorAuthError
    from app.services import eqldata_client

    monkeypatch.setattr(
        eqldata_client.requests, "post",
        lambda *a, **kw: _FakeResponse(401, {"message": "bad creds"}),
    )
    with pytest.raises(VendorAuthError):
        eqldata_client.generate_auth_token("e@x.com", "wrong", "https://api.equalsolution.net")


def test_generate_auth_token_raises_on_network_error(monkeypatch):
    import requests as requests_module

    from app.exceptions import VendorAuthError
    from app.services import eqldata_client

    def _raise(*a, **kw):
        raise requests_module.exceptions.ConnectionError("no route to host")

    monkeypatch.setattr(eqldata_client.requests, "post", _raise)
    with pytest.raises(VendorAuthError):
        eqldata_client.generate_auth_token("e@x.com", "pw", "https://api.equalsolution.net")


def test_get_instrument_list_returns_dict(monkeypatch):
    from app.services import eqldata_client

    monkeypatch.setattr(
        eqldata_client.requests, "post",
        lambda *a, **kw: _FakeResponse(200, {"NFOFUT": ["NFOFUT:ABB_I", "NFOFUT:ABB26AUGFUT"]}),
    )
    result = eqldata_client.get_instrument_list("tok", "https://api.equalsolution.net")
    assert result == {"NFOFUT": ["NFOFUT:ABB_I", "NFOFUT:ABB26AUGFUT"]}


def test_get_instrument_list_raises_on_non_200(monkeypatch):
    from app.exceptions import VendorApiError
    from app.services import eqldata_client

    monkeypatch.setattr(eqldata_client.requests, "post", lambda *a, **kw: _FakeResponse(500, {}))
    with pytest.raises(VendorApiError):
        eqldata_client.get_instrument_list("tok", "https://api.equalsolution.net")


def _make_eod_range_zip(csv_text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("EOD_RANGE.csv", csv_text)
        z.writestr("EOD_RANGE_META.json", "{}")
    return buf.getvalue()


def test_fetch_eod_range_rows_parses_zip_csv(monkeypatch):
    from app.services import eqldata_client

    csv_text = "INSTRUMENT,DTYYYYMMDD,OPEN,HIGH,LOW,CLOSE,VOL,OPENINT\nABB_I,20260818,100,105,95,102,1000,500\n"
    zip_bytes = _make_eod_range_zip(csv_text)
    monkeypatch.setattr(
        eqldata_client.requests, "post",
        lambda *a, **kw: _FakeResponse(200, content=zip_bytes),
    )
    rows = eqldata_client.fetch_eod_range_rows(
        "tok", ["NFOFUT:ABB_I"], "2026-08-18", "2026-08-18", "https://api.equalsolution.net",
    )
    assert rows == [{
        "INSTRUMENT": "ABB_I", "DTYYYYMMDD": "20260818", "OPEN": "100", "HIGH": "105",
        "LOW": "95", "CLOSE": "102", "VOL": "1000", "OPENINT": "500",
    }]


def test_fetch_eod_range_rows_empty_content_returns_empty_list(monkeypatch):
    from app.services import eqldata_client

    monkeypatch.setattr(eqldata_client.requests, "post", lambda *a, **kw: _FakeResponse(200, content=b""))
    assert eqldata_client.fetch_eod_range_rows("tok", [], "2026-08-18", "2026-08-18", "base") == []


def test_fetch_eod_range_rows_raises_rate_limit_with_retry_after(monkeypatch):
    from app.services import eqldata_client
    from app.services.eqldata_client import VendorRateLimitError

    monkeypatch.setattr(
        eqldata_client.requests, "post",
        lambda *a, **kw: _FakeResponse(429, {"message": "quota exceeded", "retry_after_seconds": 3600}),
    )
    with pytest.raises(VendorRateLimitError) as exc_info:
        eqldata_client.fetch_eod_range_rows("tok", ["NFOFUT:ABB_I"], "2026-08-18", "2026-08-18", "base")
    assert exc_info.value.retry_after_seconds == 3600


def test_fetch_eod_range_rows_raises_on_bad_zip(monkeypatch):
    from app.exceptions import VendorApiError
    from app.services import eqldata_client

    monkeypatch.setattr(
        eqldata_client.requests, "post",
        lambda *a, **kw: _FakeResponse(200, content=b"not a zip"),
    )
    with pytest.raises(VendorApiError):
        eqldata_client.fetch_eod_range_rows("tok", ["NFOFUT:ABB_I"], "2026-08-18", "2026-08-18", "base")


# ── app.repositories.instrument_repo: vendor-ingest functions ───────────────

def test_get_latest_trade_date_queries_max():
    from app.repositories.instrument_repo import get_latest_trade_date

    captured = {}

    class _FakeResult:
        def scalar_one_or_none(self):
            return date(2026, 8, 18)

    class _FakeSession:
        async def execute(self, stmt):
            captured["sql"] = str(stmt)
            return _FakeResult()

    result = asyncio.run(get_latest_trade_date(_FakeSession()))
    assert result == date(2026, 8, 18)
    assert "max" in captured["sql"].lower()


def test_get_or_create_instruments_empty_list_skips_query():
    from app.repositories.instrument_repo import get_or_create_instruments

    class _FakeSession:
        async def execute(self, stmt):
            raise AssertionError("should not query for an empty symbol list")

    assert asyncio.run(get_or_create_instruments(_FakeSession(), [])) == {}


def test_bulk_upsert_eod_bars_batches_large_input():
    from app.repositories.instrument_repo import _EOD_BAR_UPSERT_BATCH_SIZE, bulk_upsert_eod_bars

    calls = []

    class _FakeResult:
        rowcount = 1

    class _FakeSession:
        async def execute(self, stmt):
            calls.append(1)
            return _FakeResult()

    rows = [
        {
            "instrument_id": i, "trade_date": date(2026, 8, 18), "open": 1.0, "high": 1.0,
            "low": 1.0, "close": 1.0, "volume": 1, "open_interest": None, "source": "eqldata",
        }
        for i in range(_EOD_BAR_UPSERT_BATCH_SIZE + 5)
    ]
    total = asyncio.run(bulk_upsert_eod_bars(_FakeSession(), rows))
    assert len(calls) == 2  # one full batch + one partial batch
    assert total == 2  # sum of each fake execute's rowcount=1


def test_bulk_upsert_eod_bars_noop_on_empty_list():
    from app.repositories.instrument_repo import bulk_upsert_eod_bars

    class _FakeSession:
        async def execute(self, stmt):
            raise AssertionError("should not execute for an empty row list")

    assert asyncio.run(bulk_upsert_eod_bars(_FakeSession(), [])) == 0


def test_bulk_upsert_trading_calendar_noop_on_empty_set():
    from app.repositories.instrument_repo import bulk_upsert_trading_calendar

    class _FakeSession:
        async def execute(self, stmt):
            raise AssertionError("should not execute for an empty date set")

    asyncio.run(bulk_upsert_trading_calendar(_FakeSession(), set()))  # must not raise


def test_update_instrument_date_bounds_noop_on_empty_list():
    from app.repositories.instrument_repo import update_instrument_date_bounds

    class _FakeSession:
        async def execute(self, stmt, params=None):
            raise AssertionError("should not execute for an empty bounds list")

    asyncio.run(update_instrument_date_bounds(_FakeSession(), []))  # must not raise


def test_update_instrument_date_bounds_executes_on_core_connection():
    """Regression for two real prod 500s in a row: update(<mapped class>)
    executed via session.execute() with a list of param dicts ALWAYS routes
    through SQLAlchemy's ORM bulk-persistence machinery, no matter what
    execution_options or WHERE clause it carries — which then insists on
    its own "bulk UPDATE by Primary Key" dict-key convention and raises
    InvalidRequestError otherwise (a synchronize_session=None fix alone,
    tried first, wasn't enough — see this function's own comment for the
    full story). Only session.connection().execute() (Core, no ORM
    interpretation at all) actually works for a bindparam-WHERE
    executemany UPDATE like this one."""
    from app.repositories.instrument_repo import update_instrument_date_bounds

    captured = {}

    class _FakeConnection:
        async def execute(self, stmt, params=None):
            captured["params"] = params

    class _FakeSession:
        async def connection(self):
            return _FakeConnection()

        async def execute(self, stmt, params=None):
            raise AssertionError("must not call session.execute() directly — see docstring")

    asyncio.run(update_instrument_date_bounds(
        _FakeSession(), [{"id": 1, "first_traded_date": date(2026, 1, 1), "last_traded_date": date(2026, 1, 2)}],
    ))
    assert captured["params"] == [
        {"_id": 1, "first_traded_date": date(2026, 1, 1), "last_traded_date": date(2026, 1, 2)},
    ]


# ── app.services.inception_vendor_sync_service: pure helpers ────────────────

def test_continuous_futures_filters_and_strips_prefix():
    from app.services.inception_vendor_sync_service import _continuous_futures

    items = ["NFOFUT:ABB_I", "NFOFUT:ABB_II", "NFOFUT:ABB26AUGFUT", "NFOFUT:TCS26SEPFUT"]
    assert _continuous_futures(items) == ["ABB_I", "ABB_II"]


def test_parse_row_returns_bar_dict_for_known_instrument():
    from app.services.inception_vendor_sync_service import _parse_row

    class _Instr:
        id = 42

    raw = {
        "INSTRUMENT": "ABB_I", "DTYYYYMMDD": "20260818", "OPEN": "100", "HIGH": "105",
        "LOW": "95", "CLOSE": "102", "VOL": "1000", "OPENINT": "500",
    }
    parsed = _parse_row(raw, {"ABB_I": _Instr()})
    assert parsed["trade_date"] == date(2026, 8, 18)
    assert parsed["instrument_id"] == 42
    assert parsed["bar"] == {
        "instrument_id": 42, "trade_date": date(2026, 8, 18), "open": 100.0, "high": 105.0,
        "low": 95.0, "close": 102.0, "volume": 1000, "open_interest": 500, "source": "eqldata",
    }


def test_parse_row_handles_blank_open_interest():
    from app.services.inception_vendor_sync_service import _parse_row

    class _Instr:
        id = 1

    raw = {
        "INSTRUMENT": "ABB_I", "DTYYYYMMDD": "20260818", "OPEN": "1", "HIGH": "1",
        "LOW": "1", "CLOSE": "1", "VOL": "1", "OPENINT": "",
    }
    parsed = _parse_row(raw, {"ABB_I": _Instr()})
    assert parsed["bar"]["open_interest"] is None


def test_parse_row_returns_none_for_unknown_instrument():
    from app.services.inception_vendor_sync_service import _parse_row

    raw = {"INSTRUMENT": "UNKNOWN_I", "DTYYYYMMDD": "20260818", "OPEN": "1", "HIGH": "1", "LOW": "1", "CLOSE": "1", "VOL": "1", "OPENINT": ""}
    assert _parse_row(raw, {}) is None


def test_parse_row_returns_none_for_unparseable_date():
    from app.services.inception_vendor_sync_service import _parse_row

    class _Instr:
        id = 1

    raw = {"INSTRUMENT": "ABB_I", "DTYYYYMMDD": "not-a-date", "OPEN": "1", "HIGH": "1", "LOW": "1", "CLOSE": "1", "VOL": "1", "OPENINT": ""}
    assert _parse_row(raw, {"ABB_I": _Instr()}) is None


def test_chunk_dates_respects_max_days():
    from app.services.inception_vendor_sync_service import _chunk_dates

    chunks = _chunk_dates(date(2026, 1, 1), date(2026, 1, 10), max_days=3)
    assert chunks == [
        (date(2026, 1, 1), date(2026, 1, 3)),
        (date(2026, 1, 4), date(2026, 1, 6)),
        (date(2026, 1, 7), date(2026, 1, 9)),
        (date(2026, 1, 10), date(2026, 1, 10)),
    ]


def test_chunk_dates_single_day_range_is_one_chunk():
    from app.services.inception_vendor_sync_service import _chunk_dates

    assert _chunk_dates(date(2026, 1, 1), date(2026, 1, 1), max_days=366) == [(date(2026, 1, 1), date(2026, 1, 1))]


def test_chunk_list_batches_correctly():
    from app.services.inception_vendor_sync_service import _chunk_list

    assert _chunk_list([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
    assert _chunk_list([], 2) == []


def test_resolve_date_bounds_preserves_existing_first_traded_date():
    from app.services.inception_vendor_sync_service import _resolve_date_bounds

    class _Instr:
        id = 1
        first_traded_date = date(2013, 1, 1)
        last_traded_date = date(2026, 8, 17)

    bounds = _resolve_date_bounds(
        {"ABB_I": _Instr()}, {1: [date(2026, 8, 18), date(2026, 8, 19)]},
    )
    assert bounds == [{"id": 1, "first_traded_date": date(2013, 1, 1), "last_traded_date": date(2026, 8, 19)}]


def test_resolve_date_bounds_sets_first_traded_date_for_new_instrument():
    from app.services.inception_vendor_sync_service import _resolve_date_bounds

    class _Instr:
        id = 2
        first_traded_date = None
        last_traded_date = None

    bounds = _resolve_date_bounds(
        {"NEW_I": _Instr()}, {2: [date(2026, 8, 18), date(2026, 8, 19)]},
    )
    assert bounds == [{"id": 2, "first_traded_date": date(2026, 8, 18), "last_traded_date": date(2026, 8, 19)}]


def test_resolve_date_bounds_skips_instrument_with_no_new_dates():
    from app.services.inception_vendor_sync_service import _resolve_date_bounds

    class _Instr:
        id = 3
        first_traded_date = date(2013, 1, 1)
        last_traded_date = date(2026, 8, 17)

    # Requested but no rows came back for it this sync (e.g. a holiday-only gap).
    assert _resolve_date_bounds({"ABB_I": _Instr()}, {}) == []


# ── app.services.inception_vendor_sync_service.sync_nfofut_from_vendor ────
# Full orchestration — fakes/monkeypatches every I/O boundary (repo calls,
# eqldata_client calls) so this stays a fast, offline unit test.

class _FakeSession:
    def __init__(self):
        self.committed = False

    async def commit(self):
        self.committed = True


def test_sync_raises_when_vendor_not_configured(monkeypatch):
    from app.core.config import settings
    from app.exceptions import VendorNotConfiguredError
    from app.services import inception_vendor_sync_service as svc

    monkeypatch.setattr(settings, "eqldata_email", "")
    monkeypatch.setattr(settings, "eqldata_password", "")
    with pytest.raises(VendorNotConfiguredError):
        asyncio.run(svc.sync_nfofut_from_vendor(_FakeSession()))


def test_sync_uses_request_credentials_over_server_env(monkeypatch):
    """The desktop's Username/Password/Exchange fields — see
    VendorSyncRequest's docstring — win outright when given, so a click
    works even on a server with no EQLDATA_* env config at all."""
    from app.core.config import settings
    from app.repositories import instrument_repo
    from app.services import eqldata_client, inception_vendor_sync_service as svc

    monkeypatch.setattr(settings, "eqldata_email", "")
    monkeypatch.setattr(settings, "eqldata_password", "")

    async def _fake_get_latest(session):
        return date.today()  # already up to date -> returns before any vendor call

    monkeypatch.setattr(instrument_repo, "get_latest_trade_date", _fake_get_latest)

    captured = {}
    monkeypatch.setattr(
        eqldata_client, "generate_auth_token",
        lambda email, password, base_url: captured.update(email=email, password=password) or "tok",
    )

    result = asyncio.run(svc.sync_nfofut_from_vendor(
        _FakeSession(), email="from-field@x.com", password="field-pw", exchange="NFOFUT",
    ))
    assert result.status == "already_up_to_date"
    assert result.exchange == "NFOFUT"
    # No vendor call happened (already up to date), but not raising
    # VendorNotConfiguredError despite blank server env already proves the
    # request-supplied credentials were accepted as sufficient.
    assert captured == {}


def test_sync_falls_back_to_server_env_when_request_fields_blank(monkeypatch):
    from app.core.config import settings
    from app.exceptions import VendorNotConfiguredError
    from app.services import inception_vendor_sync_service as svc

    monkeypatch.setattr(settings, "eqldata_email", "")
    monkeypatch.setattr(settings, "eqldata_password", "")

    # Blank strings from the request count as "not given", same as None.
    with pytest.raises(VendorNotConfiguredError):
        asyncio.run(svc.sync_nfofut_from_vendor(_FakeSession(), email="", password=""))


def test_sync_raises_when_no_data_loaded_yet(monkeypatch):
    from app.core.config import settings
    from app.exceptions import VendorInitialSyncRequiredError
    from app.repositories import instrument_repo
    from app.services import inception_vendor_sync_service as svc

    monkeypatch.setattr(settings, "eqldata_email", "e@x.com")
    monkeypatch.setattr(settings, "eqldata_password", "pw")

    async def _fake_get_latest(session):
        return None

    monkeypatch.setattr(instrument_repo, "get_latest_trade_date", _fake_get_latest)

    with pytest.raises(VendorInitialSyncRequiredError):
        asyncio.run(svc.sync_nfofut_from_vendor(_FakeSession()))


def test_sync_returns_already_up_to_date_without_calling_vendor(monkeypatch):
    from app.core.config import settings
    from app.repositories import instrument_repo
    from app.services import eqldata_client, inception_vendor_sync_service as svc

    monkeypatch.setattr(settings, "eqldata_email", "e@x.com")
    monkeypatch.setattr(settings, "eqldata_password", "pw")

    async def _fake_get_latest(session):
        return date.today()

    monkeypatch.setattr(instrument_repo, "get_latest_trade_date", _fake_get_latest)

    def _fail(*a, **kw):
        raise AssertionError("must not call the vendor when already up to date")

    monkeypatch.setattr(eqldata_client, "generate_auth_token", _fail)

    result = asyncio.run(svc.sync_nfofut_from_vendor(_FakeSession()))
    assert result.status == "already_up_to_date"
    assert result.bars_written == 0
    assert result.instruments_added == 0


def test_sync_full_flow_happy_path(monkeypatch):
    from app.core.config import settings
    from app.repositories import instrument_repo
    from app.services import eqldata_client, inception_vendor_sync_service as svc

    monkeypatch.setattr(settings, "eqldata_email", "e@x.com")
    monkeypatch.setattr(settings, "eqldata_password", "pw")
    monkeypatch.setattr(settings, "eqldata_base_url", "https://api.equalsolution.net")

    last_available = date(2026, 8, 17)

    async def _fake_get_latest(session):
        return last_available

    class _ExistingInstr:
        id = 1
        first_traded_date = date(2013, 1, 1)
        last_traded_date = last_available

    class _NewInstr:
        id = 2
        first_traded_date = None
        last_traded_date = None

    async def _fake_get_or_create(session, symbols):
        assert symbols == ["ABB_I", "NEW_I"]
        return {"ABB_I": _ExistingInstr(), "NEW_I": _NewInstr()}

    upsert_calls = []

    async def _fake_bulk_upsert_bars(session, rows):
        upsert_calls.append(rows)
        return len(rows)

    calendar_calls = []

    async def _fake_bulk_upsert_calendar(session, trade_dates):
        calendar_calls.append(trade_dates)

    bounds_calls = []

    async def _fake_update_bounds(session, bounds):
        bounds_calls.append(bounds)

    monkeypatch.setattr(instrument_repo, "get_latest_trade_date", _fake_get_latest)
    monkeypatch.setattr(instrument_repo, "get_or_create_instruments", _fake_get_or_create)
    monkeypatch.setattr(instrument_repo, "bulk_upsert_eod_bars", _fake_bulk_upsert_bars)
    monkeypatch.setattr(instrument_repo, "bulk_upsert_trading_calendar", _fake_bulk_upsert_calendar)
    monkeypatch.setattr(instrument_repo, "update_instrument_date_bounds", _fake_update_bounds)

    monkeypatch.setattr(eqldata_client, "generate_auth_token", lambda *a, **kw: "tok")
    monkeypatch.setattr(
        eqldata_client, "get_instrument_list",
        lambda *a, **kw: {"NFOFUT": ["NFOFUT:ABB_I", "NFOFUT:NEW_I", "NFOFUT:ABB26AUGFUT"]},
    )

    fetch_calls = []

    def _fake_fetch(token, instruments, from_date, to_date, base_url):
        fetch_calls.append((instruments, from_date, to_date))
        return [
            {"INSTRUMENT": "ABB_I", "DTYYYYMMDD": "20260818", "OPEN": "100", "HIGH": "105",
             "LOW": "95", "CLOSE": "102", "VOL": "1000", "OPENINT": "500"},
            {"INSTRUMENT": "NEW_I", "DTYYYYMMDD": "20260818", "OPEN": "10", "HIGH": "11",
             "LOW": "9", "CLOSE": "10", "VOL": "50", "OPENINT": "20"},
        ]

    monkeypatch.setattr(eqldata_client, "fetch_eod_range_rows", _fake_fetch)

    session = _FakeSession()
    result = asyncio.run(svc.sync_nfofut_from_vendor(session))

    assert fetch_calls == [(["NFOFUT:ABB_I", "NFOFUT:NEW_I"], "2026-08-18", date.today().isoformat())]
    assert len(upsert_calls) == 1 and len(upsert_calls[0]) == 2
    assert calendar_calls == [{date(2026, 8, 18)}]
    assert bounds_calls == [[
        {"id": 1, "first_traded_date": date(2013, 1, 1), "last_traded_date": date(2026, 8, 18)},
        {"id": 2, "first_traded_date": date(2026, 8, 18), "last_traded_date": date(2026, 8, 18)},
    ]]
    assert session.committed is True
    assert result.status == "ok"
    assert result.exchange == "NFOFUT"
    assert result.date_from == date(2026, 8, 18)
    assert result.last_available_before == last_available
    assert result.last_available_after == date(2026, 8, 18)
    assert result.instruments_added == 1  # only NEW_I
    assert result.bars_written == 2
