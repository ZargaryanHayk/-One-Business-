"""Add index on users.role for task_event admin fan-out."""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_users_role", "users", ["role"])


def downgrade():
    op.drop_index("ix_users_role", table_name="users")
