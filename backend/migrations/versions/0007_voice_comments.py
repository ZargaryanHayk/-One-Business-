"""Keep a voice comment's target task through transcription and retries."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "voice_jobs",
        sa.Column("comment_task_id", sa.Uuid(), sa.ForeignKey("tasks.id"), nullable=True),
    )


def downgrade():
    # Without the target, older code would turn a voice comment into a new task.
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS(SELECT 1 FROM voice_jobs WHERE comment_task_id IS NOT NULL)")
    ):
        raise RuntimeError("Voice comment jobs must be migrated before downgrading; none deleted")
    op.drop_column("voice_jobs", "comment_task_id")
