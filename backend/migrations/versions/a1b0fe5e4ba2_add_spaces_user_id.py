"""add spaces user_id

Revision ID: a1b0fe5e4ba2
Revises: 25a290c68158
Create Date: 2026-09-23 20:11:16.193914

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b0fe5e4ba2'
down_revision: Union[str, None] = '25a290c68158'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable first — existing rows need a value before this can be
    # tightened to NOT NULL below.
    op.add_column('spaces', sa.Column('user_id', sa.Integer(), nullable=True))

    # Users are NEVER created by a migration (password is secret content —
    # only seed.py or a test fixture creates the User row, always AFTER
    # migrations run, per this project's own documented setup order:
    # "alembic upgrade head" before "seed.py"). That means this migration
    # WILL run with zero User rows on every fresh install and every fresh
    # test-database rebuild — that is the normal case, not an edge case,
    # and must be handled explicitly rather than assumed away.
    #
    # Spaces, by contrast, WERE bulk-seeded unconditionally by an earlier
    # migration (5601cda571d7), before Space had any owner concept — so a
    # fresh install/test run reaches this point with exactly one
    # pre-existing, unowned Space and zero Users.
    connection = op.get_bind()
    user_count = connection.execute(sa.text("SELECT count(*) FROM users")).scalar_one()
    space_count = connection.execute(sa.text("SELECT count(*) FROM spaces")).scalar_one()

    if user_count == 0:
        if space_count > 0:
            # No user exists to own the pre-existing space(s) — but on a
            # genuinely fresh install/test run, nothing could have
            # created content under them yet either (every write
            # requires an authenticated user, and no user exists).
            # Verified here, not assumed: refuse to delete if any
            # referencing content somehow already exists.
            referencing_counts = {
                table: connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
                for table in ("tasks", "calendar_events", "inbox_items")
            }
            if any(referencing_counts.values()):
                raise RuntimeError(
                    f"Found space(s) with no owning user, but referenced by existing "
                    f"data ({referencing_counts}) — refusing to delete an unowned space "
                    "that already has real content attached. This should not be "
                    "reachable through normal application use; resolve by hand."
                )
            connection.execute(sa.text("DELETE FROM spaces"))
        # A default space is now created lazily, tied to a real user, the
        # first time one is ensured to exist — see
        # spaces_service.get_or_create_default_space_for_user, called
        # from both seed.py and the test suite's own user-creation step,
        # mirroring how the User row itself has always been created
        # lazily rather than migration-seeded.
    elif user_count == 1:
        if space_count > 1:
            raise RuntimeError(
                f"Refusing to backfill spaces.user_id automatically: found 1 user but "
                f"{space_count} spaces — ambiguous which space(s) this one user actually "
                "owns. Resolve by hand before this migration can run."
            )
        connection.execute(sa.text("UPDATE spaces SET user_id = (SELECT id FROM users LIMIT 1)"))
    else:
        raise RuntimeError(
            f"Refusing to backfill spaces.user_id automatically: found {user_count} users "
            f"and {space_count} space(s). Ambiguous ownership must be resolved by hand "
            "(inspect the data, assign spaces.user_id directly for each row) before this "
            "migration can run — it will not guess an owner."
        )

    op.alter_column('spaces', 'user_id', nullable=False)
    op.create_index(op.f('ix_spaces_user_id'), 'spaces', ['user_id'], unique=False)
    op.create_foreign_key('fk_spaces_user_id_users', 'spaces', 'users', ['user_id'], ['id'])


def downgrade() -> None:
    op.drop_constraint('fk_spaces_user_id_users', 'spaces', type_='foreignkey')
    op.drop_index(op.f('ix_spaces_user_id'), table_name='spaces')
    op.drop_column('spaces', 'user_id')
