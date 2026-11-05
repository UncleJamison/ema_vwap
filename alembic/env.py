import os
from logging.config import fileConfig

from sqlalchemy import MetaData, pool

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
# Use raw SQLAlchemy metadata reflected from our database
target_metadata = MetaData()

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    """

    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    # DB target resolution: environment variable PG_DSN wins,
    # then the alembic.ini sqlalchemy.url (should be postgresql://...).
    # If neither is set, we error out.
    url = (
        os.environ.get("ALEMBIC_DSN")
        or os.environ.get("PG_DSN")
        or config.get_main_option("sqlalchemy.url")
    )

    if not url:
        raise RuntimeError(
            "No database URL configured. Set ALEMBIC_DSN, PG_DSN, or sqlalchemy.url in alembic.ini"
        )

    if not url.startswith("postgresql://") and not url.startswith("postgres://"):
        raise RuntimeError(
            f"Alembic expects a PostgreSQL URL (postgresql://...), got: {url}"
        )

    from sqlalchemy import create_engine

    connectable = create_engine(url, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # render_as_batch=True is for SQLite, not needed for PostgreSQL
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
