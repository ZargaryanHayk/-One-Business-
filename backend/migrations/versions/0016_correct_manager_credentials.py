"""Correct the seeded project manager email and password spelling to the hire.me domain."""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

OLD_EMAIL = "test1@hier.me"
NEW_EMAIL = "test1@hire.me"
OLD_HASH = (
    "$argon2id$v=19$m=19456,t=2,p=1$+sYaiy+EIyoREdhlERzcYw$"
    "4bzorey/0iaKzIdVkUueDtCA6fL+PTKD8HLFXWuJWHs"
)
NEW_HASH = (
    "$argon2id$v=19$m=19456,t=2,p=1$3cy+NZSofXUsD19sia5oLw$"
    "cY6c+m5L4NDv0anEliKuIHoaSKRHOgFvZo9prUnDLT0"
)


def change_credentials(email: str, replacement_email: str, previous: str, replacement: str) -> None:
    op.execute(
        sa.text(
            "UPDATE users SET email = :replacement_email "
            "WHERE id = '91c928a4-a742-4e97-9866-bbf16dbd2011' AND email = :email "
            "AND NOT EXISTS (SELECT 1 FROM users WHERE email = :replacement_email)"
        ).bindparams(email=email, replacement_email=replacement_email)
    )
    # Preserve credentials changed independently after the original seed migration.
    op.execute(
        sa.text(
            "UPDATE users SET password_hash = :replacement "
            "WHERE id = '91c928a4-a742-4e97-9866-bbf16dbd2011' AND password_hash = :previous"
        ).bindparams(previous=previous, replacement=replacement)
    )


def upgrade():
    change_credentials(OLD_EMAIL, NEW_EMAIL, OLD_HASH, NEW_HASH)


def downgrade():
    change_credentials(NEW_EMAIL, OLD_EMAIL, NEW_HASH, OLD_HASH)
