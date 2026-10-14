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

    Calls to context.execute() here emit the given string to the
    script output.

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
    # DB target resolution (ema_vwap-y7a3): ALEMBIC_DB_PATH wins, then DB_PATH,
    # then the alembic.ini sqlalchemy.url. The parent directory is created so a
    # fresh file works out of the box.
    db_path = os.environ.get("ALEMBIC_DB_PATH") or os.environ.get("DB_PATH")
    if db_path:
        url = f"sqlite:///{db_path}"
    else:
        url = config.get_main_option("sqlalchemy.url")

    from sqlalchemy import create_engine

    db_file = url.split("///")[-1] if url.startswith("sqlite:///") else None
    if db_file and db_file not in (":memory:",):
        parent = os.path.dirname(os.path.abspath(db_file))
        if parent:
            os.makedirs(parent, exist_ok=True)

    connectable = create_engine(url, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
