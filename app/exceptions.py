from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.core.logging import get_logger

logger = get_logger(__name__)


class AppError(Exception):
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


class TenantNotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "tenant_not_found"


class UserNotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "user_not_found"


class DuplicateEmailError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "duplicate_email"


class InvalidCredentialsError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "invalid_credentials"


class SchemaProvisioningError(AppError):
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    code = "schema_provisioning_failed"


class InvalidTradeDateError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "invalid_trade_date"


class InvalidDateRangeError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "invalid_date_range"


class HolidayNotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "holiday_not_found"


class DuplicateHolidayDateError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "duplicate_holiday_date"


class TradeDateIsHolidayError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "trade_date_is_holiday"


class InvalidOpeningRangeError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "invalid_opening_range"


class EmailDeliveryError(AppError):
    status_code = status.HTTP_502_BAD_GATEWAY
    code = "email_delivery_failed"


class VendorNotConfiguredError(AppError):
    """Settings.eqldata_email/eqldata_password are blank — the "Fetch from
    Equal Solution" button (app/services/inception_vendor_sync_service.py)
    was clicked in an environment that hasn't set them up (see .env.example).
    """
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    code = "vendor_not_configured"


class VendorAuthError(AppError):
    """Equal Solution rejected the configured email/password."""
    status_code = status.HTTP_502_BAD_GATEWAY
    code = "vendor_auth_failed"


class VendorApiError(AppError):
    """Equal Solution's API returned an error (network failure, unexpected
    response shape, or a rate/quota limit — see detail for which)."""
    status_code = status.HTTP_502_BAD_GATEWAY
    code = "vendor_api_failed"


class VendorInitialSyncRequiredError(AppError):
    """The central EodBar table is completely empty — the "Fetch from Equal
    Solution" button only does small incremental top-ups (last available
    date -> today) synchronously within one request; a from-scratch
    multi-decade historical load needs the offline, resumable
    inception-stock-data/eod_backfill.py + load_eod_data.py process
    instead (see docs/INCEPTION_DATA.md)."""
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "vendor_initial_sync_required"


class StrategyNotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "strategy_not_found"


class FormulaVariableNotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "formula_variable_not_found"


class InvalidPageSizeError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "invalid_page_size"


class InvalidPeriodError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "invalid_period"


class AdminOnlyError(AppError):
    """The caller is authenticated but not the specific admin account an
    endpoint is restricted to (see app.core.deps.require_admin_email) —
    e.g. POST /inception/admin/sync-lmv-metrics, gated to a single named
    account rather than a role, at the feature's own request."""
    status_code = status.HTTP_403_FORBIDDEN
    code = "admin_only"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        logger.info(
            "request_failed",
            method=request.method,
            path=request.url.path,
            status_code=exc.status_code,
            error_type=type(exc).__name__,
            error_code=exc.code,
            detail=exc.detail,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "code": exc.code},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.info(
            "request_failed",
            method=request.method,
            path=request.url.path,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_type=type(exc).__name__,
            detail=str(exc),
            exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal Server Error", "code": "internal_error"},
        )
