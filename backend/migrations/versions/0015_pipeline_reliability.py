"""Queue wizard voice through the worker; bound outbox, reminder, and recovery scans."""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

KINDS = "kind IN ('task','comment','title','edit_title','edit_text')"
TARGETS = (
    "(kind = 'comment') = (comment_task_id IS NOT NULL) AND "
    "(kind IN ('edit_title','edit_text')) = (target_task_id IS NOT NULL)"
)


def upgrade():
    op.add_column(
        "voice_jobs", sa.Column("kind", sa.String(20), nullable=False, server_default="task")
    )
    op.add_column(
        "voice_jobs",
        sa.Column("target_task_id", sa.Uuid(), sa.ForeignKey("tasks.id"), nullable=True),
    )
    op.execute("UPDATE voice_jobs SET kind = 'comment' WHERE comment_task_id IS NOT NULL")
    op.create_check_constraint("voice_job_kind", "voice_jobs", KINDS)
    op.create_check_constraint("voice_job_target", "voice_jobs", TARGETS)
    op.create_index("ix_voice_jobs_status_updated", "voice_jobs", ["status", "updated_at"])
    op.add_column("outbox", sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("outbox", sa.Column("last_error", sa.String(100), nullable=True))
    op.create_index("ix_chat_cleanups_chat_id", "chat_cleanups", ["chat_id"])
    # Existing reminders were enqueued once; unsent ones become eligible for a bounded retry.
    op.add_column(
        "task_reminders",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "task_reminders",
        sa.Column(
            "queued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_task_reminders_unsent",
        "task_reminders",
        ["queued_at"],
        postgresql_where=sa.text("sent_at IS NULL"),
    )


def downgrade():
    pending = sa.text(
        "SELECT EXISTS(SELECT 1 FROM voice_jobs WHERE kind IN ('title','edit_title','edit_text')"
        " AND status IN ('queued','processing','failed'))"
    )
    if op.get_bind().scalar(pending):
        raise RuntimeError(
            "Finish or discard unfinished voice title/edit jobs before downgrading; none deleted"
        )
    op.drop_index("ix_task_reminders_unsent", table_name="task_reminders")
    op.drop_column("task_reminders", "queued_at")
    op.drop_column("task_reminders", "attempts")
    op.drop_index("ix_chat_cleanups_chat_id", table_name="chat_cleanups")
    op.drop_column("outbox", "last_error")
    op.drop_column("outbox", "attempts")
    op.drop_index("ix_voice_jobs_status_updated", table_name="voice_jobs")
    op.drop_constraint("voice_job_target", "voice_jobs", type_="check")
    op.drop_constraint("voice_job_kind", "voice_jobs", type_="check")
    # Completed title/edit jobs already applied their result; older code would misread them.
    op.execute("DELETE FROM voice_jobs WHERE kind IN ('title','edit_title','edit_text')")
    op.drop_column("voice_jobs", "target_task_id")
    op.drop_column("voice_jobs", "kind")
