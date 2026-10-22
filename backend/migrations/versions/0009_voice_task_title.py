"""Preserve a separately entered Telegram task title during voice transcription."""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("voice_jobs", sa.Column("task_title", sa.String(120), nullable=True))


def downgrade():
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS(SELECT 1 FROM voice_jobs WHERE task_title IS NOT NULL)")
    ):
        raise RuntimeError("Voice task titles must be migrated before downgrading; none deleted")
    op.drop_column("voice_jobs", "task_title")
