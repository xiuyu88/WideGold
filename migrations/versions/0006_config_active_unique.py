"""Ensure only one ACTIVE config version per config type.

Revision ID: 0006_config_active_unique
Revises: 0005_calibration_reports
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_config_versions_active_type",
        "config_versions",
        ["config_type"],
        unique=True,
        schema="reference",
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_config_versions_active_type",
        table_name="config_versions",
        schema="reference",
    )
