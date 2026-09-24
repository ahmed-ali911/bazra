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
from app.modules.spaces import service as spaces_service


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
            user = User(password_hash=password_hash)
            db.add(user)
        else:
            user.password_hash = password_hash
        db.commit()
        db.refresh(user)

        # A default Space is created lazily, tied to this user, the same
        # way Space itself moved off migration-time bulk-seeding once it
        # needed a real owner (Checkpoint 3.2) — idempotent, safe to run
        # every time this script runs.
        spaces_service.get_or_create_default_space_for_user(db, user.id)
    finally:
        db.close()

    print("Password set.")


if __name__ == "__main__":
    main()
