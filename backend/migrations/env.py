from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import settings
from app.core.base import Base

# Every module's models must be imported here so their tables register on
# Base.metadata before autogenerate compares it against the real database —
# SQLAlchemy only populates the declarative registry for modules that have
# actually been imported somewhere in the process.
from app.modules.auth import models as auth_models  # noqa: F401,E402
from app.modules.calendar import models as calendar_models  # noqa: F401,E402
from app.modules.inbox import models as inbox_models  # noqa: F401,E402
from app.modules.life_areas import models as life_areas_models  # noqa: F401,E402
from app.modules.model_router import models as model_router_models  # noqa: F401,E402
from app.modules.spaces import models as spaces_models  # noqa: F401,E402
from app.modules.tasks import models as tasks_models  # noqa: F401,E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# A caller (e.g. a test pointing this at a separate test database) may have
# already set sqlalchemy.url on the Config object before invoking Alembic
# programmatically. Only fall back to the app's own settings otherwise —
# this is what makes the `alembic` CLI keep working unchanged.
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


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
