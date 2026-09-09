"""Shared create_async_engine(...) arguments for the central and tenant
engines, so the two never drift apart on pool sizing or the query-safety
timeouts (see app/core/config.py's "Connection pool / query safety" block
for the why behind each value)."""

from app.core.config import settings


def engine_connect_args(application_name: str) -> dict:
    """asyncpg connect args. ``server_settings`` are applied as SET commands
    on every new connection, so ``statement_timeout`` /
    ``idle_in_transaction_session_timeout`` apply to every query and
    transaction on that connection for its whole pooled lifetime —
    Postgres, not the app, enforces them, so they hold even if the event
    loop is blocked. ``application_name`` tags the connection in
    pg_stat_activity so it's obvious which engine (and app) a connection
    belongs to when diagnosing a pile-up."""
    return {
        "ssl": settings.sql_ssl_mode,
        "server_settings": {
            "application_name": application_name,
            "statement_timeout": str(settings.db_statement_timeout_ms),
            "idle_in_transaction_session_timeout": str(
                settings.db_idle_in_transaction_timeout_ms
            ),
        },
    }


def engine_pool_kwargs() -> dict:
    return {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_timeout": settings.db_pool_timeout,
        "pool_recycle": settings.db_pool_recycle,
        "pool_pre_ping": True,
    }
