"""Single-use website-to-Telegram sign-in links tied to their browser session."""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "telegram_login_tokens",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "session_hash",
            sa.String(64),
            sa.ForeignKey("sessions.token_hash", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_telegram_login_tokens_session_hash", "telegram_login_tokens", ["session_hash"]
    )
    op.create_index("ix_telegram_login_tokens_expires_at", "telegram_login_tokens", ["expires_at"])


def downgrade():
    op.drop_table("telegram_login_tokens")
