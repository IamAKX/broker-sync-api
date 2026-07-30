import sys

import pytest
from pydantic import ValidationError


def test_tenant_metadata_registers_opening_range_model():
    for module_name in [
        "app.models",
        "app.models.central",
        "app.models.tenant",
        "app.db.base",
        "app.services.provisioning_service",
    ]:
        sys.modules.pop(module_name, None)

    import app.models  # noqa: F401

    from app.db.base import TenantBase

    assert "OpeningRangeCapture" in TenantBase.metadata.tables
    columns = {c.name for c in TenantBase.metadata.tables["OpeningRangeCapture"].columns}
    assert columns == {"trade_date", "stock_id", "high", "low", "window_minutes", "updated_at"}


def test_opening_range_routes_registered():
    from app.main import create_app

    app = create_app()
    paths = {r.path for r in app.routes if hasattr(r, "path")}
    assert "/opening-range/daily-upload" in paths
    assert "/opening-range/snapshot" in paths
    assert "/opening-range/latest" in paths
    assert "/opening-range/availability" in paths
    assert "/opening-range/{trade_date}" in paths


def test_upload_request_rejects_zero_window_minutes():
    from app.schemas.opening_range import OpeningRangeUploadRequest

    with pytest.raises(ValidationError):
        OpeningRangeUploadRequest(trade_date="2026-01-05", window_minutes=0, rows=[])


def test_upload_request_rejects_window_minutes_over_max():
    from app.schemas.opening_range import OpeningRangeUploadRequest

    with pytest.raises(ValidationError):
        OpeningRangeUploadRequest(trade_date="2026-01-05", window_minutes=391, rows=[])


def test_upload_request_accepts_valid_window_minutes():
    from app.schemas.opening_range import OpeningRangeUploadRequest, OpeningRangeUploadRow

    req = OpeningRangeUploadRequest(
        trade_date="2026-01-05",
        window_minutes=15,
        rows=[OpeningRangeUploadRow(symbol="INFY", high=1810.5, low=1795.0)],
    )
    assert req.window_minutes == 15
    assert req.rows[0].symbol == "INFY"
