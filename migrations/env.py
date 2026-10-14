from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

import app.models  # noqa: F401  (registers the tables on Base.metadata)
from app.config import settings
from app.db import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def database_url() -> str:
    # Precedence: `alembic -x url=...`, then a URL set by code (the tests),
    # then the app's DATABASE_URL setting.
    return (
        context.get_x_argument(as_dictionary=True).get("url")
        or config.get_main_option("sqlalchemy.url")
        or settings.database_url
    )


def run_migrations_offline() -> None:
    context.configure(url=database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
