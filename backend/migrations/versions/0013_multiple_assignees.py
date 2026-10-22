"""Support additional assignees while preserving existing task assignments."""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "task_assignees",
        sa.Column(
            "task_id", sa.Uuid(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), primary_key=True),
    )
    op.create_index("ix_task_assignees_user_id", "task_assignees", ["user_id"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM task_assignees)")):
        raise RuntimeError(
            "Reduce tasks to one assignee before downgrading; no assignments deleted"
        )
    op.drop_table("task_assignees")
