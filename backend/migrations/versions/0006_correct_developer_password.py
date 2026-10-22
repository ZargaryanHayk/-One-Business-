"""Correct the seeded developer password spelling to match its email domain."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

OLD_HASH = (
    "$argon2id$v=19$m=19456,t=2,p=1$b4h1D+rCB2VntfIOLZM7vg$"
    "sNT2vp/rXIunjjEVlp+ViMeLOUKEWjovCeEcCkIDyyQ"
)
NEW_HASH = (
    "$argon2id$v=19$m=19456,t=2,p=1$JaxwGsjdjDbiinHKZslmnA$"
    "8QN0QTECdGEKkW5ErZ0pwZ0oYRurREOkNdznX6ljVEo"
)


def change_password(previous: str, replacement: str) -> None:
    # Preserve credentials changed independently after the original seed migration.
    op.execute(
        sa.text(
            "UPDATE users SET password_hash = :replacement "
            "WHERE id = '91c928a4-a742-4e97-9866-bbf16dbd2012' "
            "AND email = 'test2@hire.me' AND password_hash = :previous"
        ).bindparams(previous=previous, replacement=replacement)
    )


def upgrade():
    change_password(OLD_HASH, NEW_HASH)


def downgrade():
    change_password(NEW_HASH, OLD_HASH)
