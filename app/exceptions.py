from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse


class AppError(Exception):
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


class TenantNotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "tenant_not_found"


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


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "code": exc.code},
        )
