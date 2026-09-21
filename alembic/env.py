import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# Make the app package importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.backend.db.database import Base, DATABASE_URL
import app.backend.models.db_models  # noqa: F401 — registers all models

config = context.config
# ConfigParser interpolation treats '%' as syntax; keep real URL characters.
config.set_main_option("sqlalchemy.url", DATABASE_URL.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _detach_phase2_columns_from_live_metadata() -> None:
    """Keep 001's create_all from materializing columns owned by 081.

    Revision 001 calls Base.metadata.create_all() on a subset of live ORM
    tables. Phase 2 columns must not appear until 081.
    """
    deferred = {
        "screening_results": ("current_decision_id",),
        "training_examples": ("screening_decision_id",),
        "ai_decision_logs": ("screening_decision_id",),
    }
    for table_name, columns in deferred.items():
        table = Base.metadata.tables.get(table_name)
        if table is None:
            continue
        for name in columns:
            if name in table.c:
                table._columns.remove(table.c[name])
        for index in list(table.indexes):
            if any(getattr(col, "name", None) in columns for col in index.columns):
                table.indexes.discard(index)


_detach_phase2_columns_from_live_metadata()


def run_migrations_offline() -> None:
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
