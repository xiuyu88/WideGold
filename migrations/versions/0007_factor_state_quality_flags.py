"""factor state quality flags

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "factor_states",
        sa.Column(
            "quality_flags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        schema="decision",
    )
    op.alter_column(
        "factor_states", "quality_flags", schema="decision", server_default=None
    )


def downgrade() -> None:
    op.drop_column("factor_states", "quality_flags", schema="decision")
