from sqlalchemy.orm import Session

from app.modules.spaces import service


def test_get_default_space_resolves_the_migration_seeded_row(db_session: Session) -> None:
    space = service.get_default_space(db_session)
    assert space.is_default is True
    assert space.name
