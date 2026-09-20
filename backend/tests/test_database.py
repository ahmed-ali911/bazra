from sqlalchemy import text
from sqlalchemy.orm import Session


def test_db_session_round_trip(db_session: Session) -> None:
    result = db_session.execute(text("SELECT 1")).scalar()
    assert result == 1
