"""Calibration report persistence.

Revision ID: 0005
Revises: 0004
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''
    CREATE TABLE research.calibration_reports (
      calibration_id UUID PRIMARY KEY,
      start_date DATE NOT NULL,
      end_date DATE NOT NULL,
      horizons JSONB NOT NULL,
      requested_by UUID,
      result_json JSONB NOT NULL,
      created_at TIMESTAMPTZ NOT NULL
    );
    CREATE INDEX ix_calibration_reports_range
      ON research.calibration_reports(start_date, end_date, created_at);
    ''')


def downgrade():
    op.execute('DROP TABLE IF EXISTS research.calibration_reports CASCADE;')
