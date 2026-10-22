"""Track Telegram message identifiers and durable logout cleanup."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "telegram_messages",
        sa.Column("chat_id", sa.BigInteger(), primary_key=True),
        sa.Column("message_id", sa.BigInteger(), primary_key=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_telegram_messages_sent_at", "telegram_messages", ["sent_at"])
    op.create_table(
        "chat_cleanups",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_ids", JSONB(), nullable=False),
        sa.Column("reset_done", sa.Boolean(), nullable=False),
        sa.Column("single_delete", sa.Boolean(), nullable=False),
        sa.Column("scan_legacy", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    # Existing task/comment/voice origins are the only historical message IDs available.
    for table in ("tasks", "voice_jobs", "task_comments"):
        op.execute(
            sa.text(f"""
            INSERT INTO telegram_messages (chat_id, message_id, sent_at, observed_at)
            SELECT chat_id, message_id, created_at, created_at FROM {table}
            WHERE chat_id > 0 AND message_id > 0
              AND created_at > now() - interval '48 hours'
            ON CONFLICT DO NOTHING
        """)
        )


def downgrade():
    op.drop_table("chat_cleanups")
    op.drop_index("ix_telegram_messages_sent_at", table_name="telegram_messages")
    op.drop_table("telegram_messages")
