"""Separate short task headlines from their complete descriptions."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "tasks", sa.Column("title", sa.String(120), nullable=False, server_default="Untitled task")
    )
    # Preserve the complete original text; normalize whitespace only in the new headline.
    op.execute(
        sa.text(r"""
        UPDATE tasks SET title = CASE
            WHEN length(headline) = 0 THEN 'Untitled task'
            WHEN length(headline) > 120 THEN rtrim(left(headline, 119)) || '…'
            ELSE headline END
        FROM (
            SELECT id, btrim(regexp_replace(text, '\s+', ' ', 'g')) AS headline FROM tasks
        ) AS original WHERE tasks.id = original.id
        """)
    )


def downgrade():
    op.drop_column("tasks", "title")
