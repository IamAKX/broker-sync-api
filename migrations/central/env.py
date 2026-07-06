from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.core.config import settings
from app.db.base import CentralBase
from app.models import central  # noqa: F401  (registers models on CentralBase.metadata)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The connection URL is built directly from Settings and passed straight to
# create_engine/context.configure, bypassing Alembic's ConfigParser-backed
# set_main_option/engine_from_config entirely. That's deliberate: ConfigParser's
# default interpolation treats "%" as special, and a password containing a
# percent-encoded character (e.g. "#" -> "%23") would otherwise raise
# "invalid interpolation syntax" when set via set_main_option.
_DB_URL = settings.sql_sync_connection_url

target_metadata = CentralBase.metadata


def run_migrations_offline() -> None:
    context.configure(url=_DB_URL, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(_DB_URL, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
