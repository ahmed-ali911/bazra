from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine


def test_alembic_upgrade_head(test_engine: Engine) -> None:
    """The session-scoped _prepare_test_database fixture already ran every
    migration once against this database, so this proves the second thing
    that matters: upgrading to head again is a safe no-op, not an error.
    """
    config = Config("alembic.ini")
    # str(test_engine.url) masks the password with '***' by default — that would
    # silently hand Alembic a connection string it can't actually authenticate with.
    config.set_main_option("sqlalchemy.url", test_engine.url.render_as_string(hide_password=False))
    command.upgrade(config, "head")
