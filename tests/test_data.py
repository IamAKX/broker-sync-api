import pytest


@pytest.fixture()
async def access_token(client, unique_email, unique_name):
    response = await client.post(
        "/auth/signup",
        json={"name": unique_name, "email": unique_email, "password": "Str0ngPassw0rd!"},
    )
    return response.json()["access_token"]


async def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_daily_upload_and_snapshot_round_trip(client, access_token):
    headers = await _auth_headers(access_token)

    upload = await client.post(
        "/data/daily-upload",
        headers=headers,
        json={
            "trade_date": "2026-06-27",
            "rows": [
                {"symbol": "RADICO", "display_name": "Radico Khaitan Limited", "metrics": {"PMC": 3554.9, "VAH": 3554.9}}
            ],
        },
    )
    assert upload.status_code == 200
    assert upload.json()["values_upserted"] == 2

    snapshot = await client.get("/data/snapshot", headers=headers, params={"date": "2026-06-27"})
    assert snapshot.status_code == 200
    body = snapshot.json()
    assert body["trade_date"] == "2026-06-27"
    stock = next(s for s in body["stocks"] if s["symbol"] == "RADICO")
    assert stock["metrics"]["PMC"] == 3554.9
    assert stock["metrics"]["VAH"] == 3554.9


async def test_missing_metric_returns_null_not_zero(client, access_token):
    headers = await _auth_headers(access_token)

    await client.post(
        "/data/daily-upload",
        headers=headers,
        json={"trade_date": "2026-06-28", "rows": [{"symbol": "RADICO", "metrics": {"PMC": 100.0}}]},
    )
    # Second day: VAH is uploaded but PMC is not — PMC for this date should be absent/null,
    # not 0, and should not overwrite day 1's PMC.
    await client.post(
        "/data/daily-upload",
        headers=headers,
        json={"trade_date": "2026-06-29", "rows": [{"symbol": "RADICO", "metrics": {"VAH": 200.0}}]},
    )

    snapshot_day2 = await client.get("/data/snapshot", headers=headers, params={"date": "2026-06-29"})
    metrics_day2 = snapshot_day2.json()["stocks"][0]["metrics"]
    assert metrics_day2.get("PMC") is None
    assert metrics_day2["VAH"] == 200.0

    snapshot_day1 = await client.get("/data/snapshot", headers=headers, params={"date": "2026-06-28"})
    metrics_day1 = snapshot_day1.json()["stocks"][0]["metrics"]
    assert metrics_day1["PMC"] == 100.0


async def test_reupload_overwrites_only_included_metrics(client, access_token):
    headers = await _auth_headers(access_token)

    await client.post(
        "/data/daily-upload",
        headers=headers,
        json={"trade_date": "2026-06-30", "rows": [{"symbol": "RADICO", "metrics": {"PMC": 1.0, "VAH": 2.0}}]},
    )
    await client.post(
        "/data/daily-upload",
        headers=headers,
        json={"trade_date": "2026-06-30", "rows": [{"symbol": "RADICO", "metrics": {"PMC": 99.0}}]},
    )

    snapshot = await client.get("/data/snapshot", headers=headers, params={"date": "2026-06-30"})
    metrics = snapshot.json()["stocks"][0]["metrics"]
    assert metrics["PMC"] == 99.0
    assert metrics["VAH"] == 2.0


async def test_timeseries_across_dates(client, access_token):
    headers = await _auth_headers(access_token)

    for day, value in [("2026-06-01", 10.0), ("2026-06-02", 20.0), ("2026-06-03", 30.0)]:
        await client.post(
            "/data/daily-upload",
            headers=headers,
            json={"trade_date": day, "rows": [{"symbol": "RADICO", "metrics": {"PMC": value}}]},
        )

    response = await client.get(
        "/data/timeseries",
        headers=headers,
        params={"symbol": "RADICO", "metric": "PMC", "from": "2026-06-01", "to": "2026-06-03"},
    )
    assert response.status_code == 200
    points = response.json()["points"]
    assert [p["value"] for p in points] == [10.0, 20.0, 30.0]


async def test_latest_returns_most_recent_trade_date(client, access_token):
    headers = await _auth_headers(access_token)

    await client.post(
        "/data/daily-upload",
        headers=headers,
        json={"trade_date": "2026-05-01", "rows": [{"symbol": "RADICO", "metrics": {"PMC": 1.0}}]},
    )
    await client.post(
        "/data/daily-upload",
        headers=headers,
        json={"trade_date": "2026-05-05", "rows": [{"symbol": "RADICO", "metrics": {"PMC": 5.0}}]},
    )

    latest = await client.get("/data/latest", headers=headers)
    assert latest.json()["trade_date"] == "2026-05-05"


async def test_metrics_and_stocks_listing(client, access_token):
    headers = await _auth_headers(access_token)
    await client.post(
        "/data/daily-upload",
        headers=headers,
        json={"trade_date": "2026-06-27", "rows": [{"symbol": "RADICO", "metrics": {"PMC": 1.0}}]},
    )

    metrics = await client.get("/data/metrics", headers=headers)
    assert any(m["name"] == "PMC" for m in metrics.json())

    stocks = await client.get("/data/stocks", headers=headers)
    assert any(s["symbol"] == "RADICO" for s in stocks.json())
