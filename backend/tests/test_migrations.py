from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine


def test_alembic_upgrade_head(test_engine: Engine) -> None:
    """Proves migrations/env.py actually connects and runs against a clean
    database — even though there are no migration files yet, since no
    domain model exists to generate one from.
    """
    config = Config("alembic.ini")
    # str(test_engine.url) masks the password with '***' by default — that would
    # silently hand Alembic a connection string it can't actually authenticate with.
    config.set_main_option("sqlalchemy.url", test_engine.url.render_as_string(hide_password=False))
    command.upgrade(config, "head")
