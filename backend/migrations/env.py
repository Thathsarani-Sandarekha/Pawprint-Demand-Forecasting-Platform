"""Migrations for the single local PostgreSQL operational database."""
from logging.config import fileConfig
from alembic import context
from sqlalchemy import create_engine, pool
from app.db.connection import get_database_url
from app.db.models import metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def require_url() -> str:
    url = get_database_url()
    if not url:
        raise RuntimeError("DATABASE_URL not configured. Set it in the project-root .env file first.")
    return url


def run_migrations_offline() -> None:
    context.configure(url=require_url(), target_metadata=metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"}, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(require_url(), poolclass=pool.NullPool, connect_args={"connect_timeout": 3})
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
