from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from app.db.deps import get_tenant_db
from app.main import create_app
from app.schemas.historic import SnapshotResponse

# lmv-snapshot's /snapshot and /latest routes build their response as a plain
# dict and return it via a raw ORJSONResponse instead of FastAPI's
# response_model machinery (see app/routers/lmv_snapshot.py) — a deliberate
# perf tradeoff on this table's wide payloads (up to ~78 metrics/stock). That
# means a shape regression would no longer be caught per-request; this test
# is the replacement safety net, run at test time instead of on every request.

_FAKE_ROWS = [
    {
        "symbol": "NIFTY",
        "display_name": "NIFTY",
        "metric_name": "PML",
        "value_number": Decimal("23105.0000"),
        "value_text": None,
    },
    {
        "symbol": "NIFTY",
        "display_name": "NIFTY",
        "metric_name": "Trend",
        "value_number": None,
        "value_text": "flat",
    },
    {
        "symbol": "NIFTY",
        "display_name": "NIFTY",
        "metric_name": "Blank",
        "value_number": None,
        "value_text": None,
    },
]


async def _fake_get_tenant_db():
    yield None


def _build_client(monkeypatch):
    async def fake_fetch_latest_trade_date(session):
        return date(2026, 7, 23)

    async def fake_fetch_snapshot_rows(session, trade_date):
        return _FAKE_ROWS

    monkeypatch.setattr(
        "app.services.lmv_snapshot_service.fetch_latest_trade_date", fake_fetch_latest_trade_date
    )
    monkeypatch.setattr(
        "app.services.lmv_snapshot_service.fetch_snapshot_rows", fake_fetch_snapshot_rows
    )

    app = create_app()
    app.dependency_overrides[get_tenant_db] = _fake_get_tenant_db
    return TestClient(app)


def test_lmv_snapshot_latest_matches_snapshot_response_schema(monkeypatch):
    client = _build_client(monkeypatch)

    response = client.get("/lmv-snapshot/latest")

    assert response.status_code == 200
    body = response.json()

    # Round-trips through the same schema the removed response_model
    # validation used to enforce — catches a shape regression here instead of
    # on every production request.
    validated = SnapshotResponse.model_validate(body)
    assert validated.trade_date == date(2026, 7, 23)
    assert len(validated.stocks) == 1

    nifty = validated.stocks[0]
    assert nifty.symbol == "NIFTY"
    assert nifty.metrics["PML"] == 23105.0
    assert isinstance(nifty.metrics["PML"], float)
    assert nifty.metrics["Trend"] == "flat"
    assert nifty.metrics["Blank"] is None


def test_lmv_snapshot_snapshot_matches_snapshot_response_schema(monkeypatch):
    client = _build_client(monkeypatch)

    response = client.get("/lmv-snapshot/snapshot", params={"date": "2026-07-23"})

    assert response.status_code == 200
    validated = SnapshotResponse.model_validate(response.json())
    assert validated.trade_date == date(2026, 7, 23)
    assert validated.stocks[0].symbol == "NIFTY"
