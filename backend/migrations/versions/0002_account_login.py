"""Email/password accounts, explicit Telegram login, and two assessment test accounts."""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("users", "telegram_id", existing_type=sa.BigInteger(), nullable=True)
    op.add_column("users", sa.Column("email", sa.String(254)))
    op.add_column("users", sa.Column("password_hash", sa.Text()))
    op.create_unique_constraint("uq_users_email", "users", ["email"])
    op.create_table(
        "telegram_sessions",
        sa.Column("telegram_id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_telegram_sessions_user_id", "telegram_sessions", ["user_id"])
    op.create_index("ix_telegram_sessions_expires_at", "telegram_sessions", ["expires_at"])
    users = sa.table(
        "users",
        sa.column("id", sa.Uuid()),
        sa.column("name", sa.String()),
        sa.column("email", sa.String()),
        sa.column("password_hash", sa.Text()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        users,
        [
            {
                "id": uuid.UUID("91c928a4-a742-4e97-9866-bbf16dbd2011"),
                "name": "Test One",
                "email": "test1@hier.me",
                "created_at": datetime.now(UTC),
                "password_hash": (
                    "$argon2id$v=19$m=19456,t=2,p=1$+sYaiy+EIyoREdhlERzcYw$"
                    "4bzorey/0iaKzIdVkUueDtCA6fL+PTKD8HLFXWuJWHs"
                ),
            },
            {
                "id": uuid.UUID("91c928a4-a742-4e97-9866-bbf16dbd2012"),
                "name": "Test Two",
                "email": "test2@hire.me",
                "created_at": datetime.now(UTC),
                "password_hash": (
                    "$argon2id$v=19$m=19456,t=2,p=1$b4h1D+rCB2VntfIOLZM7vg$"
                    "sNT2vp/rXIunjjEVlp+ViMeLOUKEWjovCeEcCkIDyyQ"
                ),
            },
        ],
    )
    # Existing tasks stay intact; older Telegram-only sessions must authenticate anew.
    op.execute("DELETE FROM login_tokens")
    op.execute("DELETE FROM sessions")


def downgrade():
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT EXISTS(SELECT 1 FROM users WHERE telegram_id IS NULL)")):
        raise RuntimeError(
            "Map email accounts to legacy users before downgrading; account data is preserved"
        )
    op.drop_table("telegram_sessions")
    op.drop_constraint("uq_users_email", "users", type_="unique")
    op.drop_column("users", "password_hash")
    op.drop_column("users", "email")
    op.alter_column("users", "telegram_id", existing_type=sa.BigInteger(), nullable=False)
