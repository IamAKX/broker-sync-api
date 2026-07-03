import uuid
from contextlib import asynccontextmanager

import jwt
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.logging import bind_request_context, configure_logging, get_logger
from app.core.security import decode_access_token
from app.db.central_session import central_engine
from app.db.tenant_session import tenant_engine
from app.exceptions import register_exception_handlers
from app.routers import auth, data

configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await central_engine.dispose()
    await tenant_engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Broker Sync API", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

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

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    register_exception_handlers(app)

    app.include_router(auth.router)
    app.include_router(data.router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
