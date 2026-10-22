"""Archive projects without changing or deleting their tasks."""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("projects", sa.Column("archived_at", sa.DateTime(timezone=True)))


def downgrade():
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS(SELECT 1 FROM projects WHERE archived_at IS NOT NULL)")
    ):
        raise RuntimeError("Restore archived projects before downgrading; no projects changed")
    op.drop_column("projects", "archived_at")
