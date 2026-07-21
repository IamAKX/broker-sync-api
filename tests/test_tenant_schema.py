import sys


def test_tenant_metadata_registers_historical_model():
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

    assert "HistoricalStockValue" in TenantBase.metadata.tables
    assert "LmvDailySnapshot" in TenantBase.metadata.tables
