"""Alembic env: reads DATABASE_URL / POSTGRES_* via app.db._build_dsn().

Both online and offline migrations target app.db.Base.metadata. We
import every model module so all tables register before autogenerate.
"""
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# app.db is on sys.path because alembic.ini prepends_sys_path = "."
from app.db import Base, _build_dsn

# Import every model module so Base.metadata sees all tables.
from app.models import audit_event, ledger_entry, payment, settlement  # noqa: F401


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout rather than connecting."""
    config.set_main_option("sqlalchemy.url", _build_dsn())
    context.configure(
        url=_build_dsn(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB."""
    config.set_main_option("sqlalchemy.url", _build_dsn())
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
