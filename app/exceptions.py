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
