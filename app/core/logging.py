import logging
from contextvars import ContextVar

import structlog

_request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
_tenant_id_var: ContextVar[str | None] = ContextVar("tenant_id", default=None)


def bind_request_context(*, request_id: str, tenant_id: str | None = None) -> None:
    _request_id_var.set(request_id)
    _tenant_id_var.set(tenant_id)


def _add_request_context(logger, method_name, event_dict):
    request_id = _request_id_var.get()
    tenant_id = _tenant_id_var.get()
    if request_id is not None:
        event_dict["request_id"] = request_id
    if tenant_id is not None:
        event_dict["tenant_id"] = tenant_id
    return event_dict


def configure_logging() -> None:
    logging.basicConfig(format="%(message)s", level=logging.INFO)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _add_request_context,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None):
    return structlog.get_logger(name)
