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
