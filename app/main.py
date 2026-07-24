import time
import uuid
from contextlib import asynccontextmanager

import jwt
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import ORJSONResponse

from app.core.config import settings
from app.core.logging import bind_request_context, configure_logging, get_logger
from app.core.security import decode_access_token
from app.db.central_session import central_engine
from app.db.tenant_session import tenant_engine
from app.exceptions import register_exception_handlers
from app.routers import auth, data, historic, holidays, lmv_snapshot

configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await central_engine.dispose()
    await tenant_engine.dispose()


def create_app() -> FastAPI:
    # orjson's C-based encoder is markedly faster than the stdlib json FastAPI uses by
    # default — matters most on wide payloads like lmv-snapshot (up to ~17k values).
    app = FastAPI(title="Broker Sync API", lifespan=lifespan, default_response_class=ORJSONResponse)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Snapshot/timeseries JSON payloads (e.g. lmv-snapshot: ~350KB at ~78 metrics x
    # 215 stocks) compress heavily — gzip cuts transfer time over the public EC2 link
    # at negligible CPU cost. 1KB floor skips wasting cycles on tiny responses.
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):
        request_id = str(uuid.uuid4())
        tenant_id = None
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            try:
                payload = decode_access_token(auth_header[7:])
                tenant_id = payload.get("tenant_id")
            except jwt.PyJWTError:
                pass
        bind_request_context(request_id=request_id, tenant_id=tenant_id)

        started_at = time.perf_counter()
        logger.info(
            "request_started",
            method=request.method,
            path=request.url.path,
            query=request.url.query,
        )

        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started_at) * 1000)
            logger.info(
                "request_failed",
                method=request.method,
                path=request.url.path,
                status_code=500,
                duration_ms=duration_ms,
                error_type=type(exc).__name__,
                detail=str(exc),
                exc_info=True,
            )
            raise

        duration_ms = int((time.perf_counter() - started_at) * 1000)
        logger.info(
            "request_completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        response.headers["X-Request-ID"] = request_id
        return response

    register_exception_handlers(app)

    app.include_router(auth.router)
    app.include_router(historic.router)
    app.include_router(data.router)
    app.include_router(holidays.router)
    app.include_router(lmv_snapshot.router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
