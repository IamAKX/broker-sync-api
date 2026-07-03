from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    environment: str = "development"

    sql_server: str
    sql_database: str
    sql_user: str
    sql_password: str
    sql_driver: str = "ODBC Driver 18 for SQL Server"

    jwt_secret: str
    jwt_access_expiry_minutes: int = 30
    jwt_refresh_expiry_days: int = 7

    cors_origins: str = "*"

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def sql_connection_url(self) -> str:
        return self._build_sql_url(dialect="mssql+aioodbc")

    @property
    def sql_sync_connection_url(self) -> str:
        """Sync (pyodbc) URL for Alembic, which does not support async engines."""
        return self._build_sql_url(dialect="mssql+pyodbc")

    def _build_sql_url(self, dialect: str) -> str:
        driver = quote_plus(self.sql_driver)
        password = quote_plus(self.sql_password)
        return (
            f"{dialect}://{self.sql_user}:{password}"
            f"@{self.sql_server}:1433/{self.sql_database}"
            f"?driver={driver}&Encrypt=yes&TrustServerCertificate=no"
        )


settings = Settings()
