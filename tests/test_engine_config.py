"""app/db/engine_config.py — the shared create_async_engine args that keep
the central and tenant engines from drifting on pool sizing / query-safety
timeouts (the "every endpoint Read timed out" pool-exhaustion incident)."""

from app.core.config import settings
from app.db.engine_config import engine_connect_args, engine_pool_kwargs


def test_connect_args_carry_postgres_query_safety_timeouts():
    server_settings = engine_connect_args("brokersync-central")["server_settings"]
    assert server_settings["statement_timeout"] == str(settings.db_statement_timeout_ms)
    assert server_settings["idle_in_transaction_session_timeout"] == str(
        settings.db_idle_in_transaction_timeout_ms
    )
    # str, not int — asyncpg passes server_settings through verbatim as SET values.
    assert isinstance(server_settings["statement_timeout"], str)


def test_connect_args_tag_the_connection_per_engine():
    assert engine_connect_args("brokersync-central")["server_settings"]["application_name"] == "brokersync-central"
    assert engine_connect_args("brokersync-tenant")["server_settings"]["application_name"] == "brokersync-tenant"


def test_connect_args_keep_ssl_mode():
    assert engine_connect_args("x")["ssl"] == settings.sql_ssl_mode


def test_pool_kwargs_use_settings_and_enable_pre_ping():
    kw = engine_pool_kwargs()
    assert kw["pool_size"] == settings.db_pool_size
    assert kw["max_overflow"] == settings.db_max_overflow
    assert kw["pool_timeout"] == settings.db_pool_timeout
    assert kw["pool_recycle"] == settings.db_pool_recycle
    assert kw["pool_pre_ping"] is True


def test_pool_timeout_is_below_the_desktop_client_read_timeout():
    # The desktop api_client uses a 15s default read timeout — a request must
    # give up waiting for a pooled connection before the client gives up on
    # the request, so a saturated pool surfaces as a fast, clear error.
    assert settings.db_pool_timeout < 15


def test_both_engines_share_the_same_pool_shape():
    from app.db.central_session import central_engine
    from app.db.tenant_session import tenant_engine

    assert central_engine.pool.size() == tenant_engine.pool.size() == settings.db_pool_size
