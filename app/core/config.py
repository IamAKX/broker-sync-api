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
    smtp_from_name: str = "Fuku Lens"

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

    # ── AI chatbot (Anthropic API) — see docs/CHATBOT.md ───────────────────
    # Disabled by default so an environment without an API key still boots;
    # POST /chat/ask returns a clear 503 until chat_enabled is turned on.
    chat_enabled: bool = False
    # Secret key from console.anthropic.com (env ANTHROPIC_API_KEY). Server-side only.
    anthropic_api_key: str = ""
    # Any Anthropic model id. Swapping models is a config change only.
    chat_model_id: str = "claude-haiku-4-5"
    # Optional separate model for the final answer step (blank = same as above).
    chat_answer_model_id: str = ""
    # Planner reply (JSON plan + SQL). Room for long multi-level SQL; a cut-off reply is unparseable.
    chat_sql_max_tokens: int = 2000
    chat_answer_max_tokens: int = 1200
    # POST /chat/translate: longest answer it accepts, and the reply budget (Tamil
    # takes several times the tokens of the same answer in English).
    chat_translate_max_chars: int = 8000
    chat_translate_max_tokens: int = 6000
    # API client timeout (seconds) and automatic retries.
    chat_llm_timeout: int = 60
    chat_llm_max_retries: int = 2
    # SQL safety limits for the model-generated query.
    chat_sql_timeout_ms: int = 8000
    chat_sql_max_rows: int = 200
    # Rows/characters of result data handed back to the model for the answer.
    chat_answer_max_rows: int = 60
    chat_answer_max_chars: int = 24000
    # One repair attempt: the model sees the validator/DB error and rewrites the SQL.
    chat_sql_max_retries: int = 1
    # Prior turns the client may send for follow-up questions.
    chat_max_history_turns: int = 6
    chat_max_question_chars: int = 1000
    # Per-user questions per minute (per worker process — see docs/CHATBOT.md).
    chat_rate_limit_per_minute: int = 12
    # Directory for the separate prompt / generated-SQL audit logs.
    chat_log_dir: str = "logs/chat"
    # Folder of Markdown knowledge-base files (see knowledge/README.md), loaded at startup.
    chat_knowledge_dir: str = "knowledge"
    # Hard cap on knowledge text put in one prompt (on-demand docs are dropped first).
    chat_knowledge_max_chars: int = 60000

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
