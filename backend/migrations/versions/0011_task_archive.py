"""Keep completed tasks in a restorable archive separate from deletion."""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tasks", sa.Column("archived_at", sa.DateTime(timezone=True)))
    op.create_check_constraint(
        "task_archive_completed", "tasks", "archived_at IS NULL OR status = 'completed'"
    )


def downgrade():
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS(SELECT 1 FROM tasks WHERE archived_at IS NOT NULL)")
    ):
        raise RuntimeError("Restore archived tasks before downgrading; no tasks changed")
    op.drop_constraint("task_archive_completed", "tasks", type_="check")
    op.drop_column("tasks", "archived_at")
