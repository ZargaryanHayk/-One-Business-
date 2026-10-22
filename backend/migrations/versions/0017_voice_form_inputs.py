"""Accept spoken answers in project and task conversations."""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

OLD_KINDS = "kind IN ('task','comment','title','edit_title','edit_text')"
NEW_KINDS = "kind IN ('task','comment','title','edit_title','edit_text','input')"


def upgrade():
    op.add_column("voice_jobs", sa.Column("input_token", sa.String(32), nullable=True))
    op.drop_constraint("voice_job_kind", "voice_jobs", type_="check")
    op.create_check_constraint("voice_job_kind", "voice_jobs", NEW_KINDS)
    op.create_check_constraint(
        "voice_job_input", "voice_jobs", "(kind = 'input') = (input_token IS NOT NULL)"
    )


def downgrade():
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM voice_jobs WHERE kind = 'input' AND status != 'completed')"
        )
    ):
        raise RuntimeError("Finish or discard unfinished voice input jobs before downgrading")
    op.execute("DELETE FROM voice_jobs WHERE kind = 'input'")
    op.drop_constraint("voice_job_input", "voice_jobs", type_="check")
    op.drop_constraint("voice_job_kind", "voice_jobs", type_="check")
    op.create_check_constraint("voice_job_kind", "voice_jobs", OLD_KINDS)
    op.drop_column("voice_jobs", "input_token")
