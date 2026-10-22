"""Dashboard creation and deletion without replaying deleted Telegram messages."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tasks", sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.alter_column("tasks", "chat_id", existing_type=sa.BigInteger(), nullable=True)
    op.alter_column("tasks", "message_id", existing_type=sa.BigInteger(), nullable=True)
    op.drop_constraint("task_source", "tasks", type_="check")
    op.create_check_constraint("task_source", "tasks", "source IN ('text','voice','dashboard')")
    op.create_check_constraint(
        "task_origin",
        "tasks",
        "(source = 'dashboard' AND chat_id IS NULL AND message_id IS NULL) OR "
        "(source IN ('text','voice') AND chat_id IS NOT NULL AND message_id IS NOT NULL)",
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM tasks WHERE source='dashboard')")):
        raise RuntimeError("Dashboard tasks must be migrated before downgrading; no tasks deleted")
    op.drop_constraint("task_origin", "tasks", type_="check")
    op.drop_constraint("task_source", "tasks", type_="check")
    op.create_check_constraint("task_source", "tasks", "source IN ('text','voice')")
    op.alter_column("tasks", "chat_id", existing_type=sa.BigInteger(), nullable=False)
    op.alter_column("tasks", "message_id", existing_type=sa.BigInteger(), nullable=False)
    op.drop_column("tasks", "deleted_at")
