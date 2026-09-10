import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from widegold.db.base import Base
from widegold.db import models  # noqa: F401

config = context.config
if os.getenv("WIDEGOLD_DATABASE_URL"):
    config.set_main_option("sqlalchemy.url", os.environ["WIDEGOLD_DATABASE_URL"])
target_metadata = Base.metadata


def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, include_schemas=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section), prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, include_schemas=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
