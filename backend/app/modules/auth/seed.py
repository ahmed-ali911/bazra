"""One-off CLI to set the single user's password.

Run via: docker compose exec backend python -m app.modules.auth.seed

Idempotent: if the User row already exists, its password_hash is updated in
place rather than inserting a second row. Safe to run again any time you
want to change the password.
"""

import getpass

from app.database import SessionLocal
from app.modules.auth import service
from app.modules.auth.models import User


def main() -> None:
    password = getpass.getpass("New password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords did not match.")
        raise SystemExit(1)

    password_hash = service.hash_password(password)

    db = SessionLocal()
    try:
        user = service.get_the_user(db)
        if user is None:
            db.add(User(password_hash=password_hash))
        else:
            user.password_hash = password_hash
        db.commit()
    finally:
        db.close()

    print("Password set.")


if __name__ == "__main__":
    main()
