from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    environment: str = "development"

    sql_server: str
    sql_database: str
    sql_user: str
    sql_password: str

    sql_ssl_mode: str = "require"

    # ── Connection pool / query safety ────────────────────────────────────
    # Two engines (central + tenant) each get their own pool of this shape,
    # both pointing at the same RDS instance. Keep the pool modest — a
    # bigger pool just queues work at the database instead of the app (see
    # app/db/central_session.py). The timeouts below are the important part:
    # they stop ONE slow/stuck query or transaction from holding a pooled
    # connection long enough to starve every other request (the "every
    # endpoint Read timed out at 15s" incident — a burst of heavy
    # /lmv-snapshot/range queries on a db.t3.micro exhausted the pool while
    # each call sat there for the client's whole timeout).
    db_pool_size: int = 5
    db_max_overflow: int = 5
    # Seconds a request waits for a free pooled connection before giving up
    # (SQLAlchemy QueuePool.pool_timeout). Kept BELOW the desktop client's
    # 15s HTTP read timeout so a saturated pool fails fast with a clear
    # error instead of the client timing out on a connection it was never
    # going to get.
    db_pool_timeout: int = 10
    # Recycle a pooled connection after this many seconds so a connection
    # silently dropped by RDS / a NAT idle timeout is replaced rather than
    # handed out dead (pool_pre_ping catches the rest).
    db_pool_recycle: int = 1800
    # Postgres kills any single statement running longer than this (ms) and
    # returns the connection to the pool, instead of it blocking for
    # minutes. 30s comfortably covers the heaviest legitimate query
    # (/lmv-snapshot/range over ~90 days) with headroom.
    db_statement_timeout_ms: int = 30000
    # Postgres kills a connection left idle inside an open transaction this
    # long (ms) — guards against a handler that errored/hung after BEGIN
    # without COMMIT/ROLLBACK pinning a connection (and any locks) forever.
    db_idle_in_transaction_timeout_ms: int = 60000

    jwt_secret: str
    jwt_access_expiry_minutes: int = 30
    jwt_refresh_expiry_days: int = 7

    cors_origins: str = "*"

    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465
    smtp_user: str
    smtp_password: str
    smtp_from: str = ""
    smtp_from_name: str = "Broker Sync"

    # Equal Solution (eqldata) vendor account — server-side only, never sent
    # to or accepted from a client (see app/services/inception_vendor_sync_
    # service.py's "Fetch from Equal Solution" feature, screens/
    # inception_settings.py's desktop button). Optional (empty default), not
    # required like sql_password/smtp_*, so an environment that hasn't set
    # these up yet (existing deployments, most local dev, CI) still starts
    # normally — the vendor-sync endpoint itself raises a clear
    # VendorNotConfiguredError if a user clicks the button without them set,
    # rather than the whole app failing to boot.
    eqldata_email: str = ""
    eqldata_password: str = ""
    eqldata_base_url: str = "https://api.equalsolution.net"

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def smtp_from_address(self) -> str:
        return self.smtp_from or self.smtp_user

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def sql_connection_url(self) -> str:
        return self._build_sql_url(dialect="postgresql+asyncpg")

    @property
    def sql_sync_connection_url(self) -> str:
        """Sync (psycopg) URL for Alembic, which does not support async engines."""
        return self._build_sql_url(dialect="postgresql+psycopg")

    def _build_sql_url(self, dialect: str) -> str:
        password = quote_plus(self.sql_password)
        return f"{dialect}://{self.sql_user}:{password}@{self.sql_server}:5432/{self.sql_database}"


settings = Settings()
