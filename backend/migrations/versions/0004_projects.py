"""Project roles, assignments, deadlines, comments, and durable reminders."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users", sa.Column("role", sa.String(20), nullable=False, server_default="developer")
    )
    op.create_check_constraint(
        "user_role", "users", "role IN ('admin','project_manager','developer')"
    )
    op.execute("UPDATE users SET role='project_manager' WHERE email='test1@hier.me'")
    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_projects_owner_id", "projects", ["owner_id"])
    op.create_table(
        "project_members",
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id"), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), primary_key=True),
    )
    for name, foreign in (("project_id", "projects.id"), ("assignee_id", "users.id")):
        op.add_column("tasks", sa.Column(name, sa.Uuid(), sa.ForeignKey(foreign)))
        op.create_index(f"ix_tasks_{name}", "tasks", [name])
    op.add_column("tasks", sa.Column("due_at", sa.DateTime(timezone=True)))
    op.create_index("ix_tasks_due_at", "tasks", ["due_at"])
    op.execute("UPDATE tasks SET assignee_id=user_id")
    for name, foreign in (("project_id", "projects.id"), ("assignee_id", "users.id")):
        op.add_column("voice_jobs", sa.Column(name, sa.Uuid(), sa.ForeignKey(foreign)))
    op.add_column("voice_jobs", sa.Column("due_at", sa.DateTime(timezone=True)))
    op.create_table(
        "task_comments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("author_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("chat_id", sa.BigInteger()),
        sa.Column("message_id", sa.BigInteger()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("chat_id", "message_id", name="uq_comment_message"),
    )
    op.create_index("ix_task_comments_task_id", "task_comments", ["task_id"])
    op.create_table(
        "task_reminders",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("recipient_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("task_id", "recipient_id", "due_at", name="uq_task_reminder"),
    )


def downgrade():
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT EXISTS(SELECT 1 FROM projects)")):
        raise RuntimeError("Migrate project data before downgrading; no projects deleted")
    if connection.scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM task_comments) OR EXISTS(SELECT 1 FROM task_reminders)"
            " OR EXISTS(SELECT 1 FROM tasks WHERE due_at IS NOT NULL)"
        )
    ):
        raise RuntimeError(
            "Migrate comments, reminders, and deadlines before downgrading; none deleted"
        )
    op.drop_table("task_reminders")
    op.drop_table("task_comments")
    for name in ("project_id", "assignee_id", "due_at"):
        op.drop_column("voice_jobs", name)
    for name in ("project_id", "assignee_id", "due_at"):
        op.drop_index(f"ix_tasks_{name}", "tasks")
        op.drop_column("tasks", name)
    op.drop_table("project_members")
    op.drop_table("projects")
    op.drop_constraint("user_role", "users", type_="check")
    op.drop_column("users", "role")
