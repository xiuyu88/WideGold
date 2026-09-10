"""Add coarse business run trace events.

Revision ID: 0003
Revises: 0002
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE ops.run_events (
            trace_seq BIGSERIAL PRIMARY KEY,
            analysis_run_id UUID NOT NULL,
            stage VARCHAR(64) NOT NULL,
            event_type VARCHAR(32) NOT NULL,
            status VARCHAR(32) NOT NULL,
            level VARCHAR(16) NOT NULL,
            message TEXT NOT NULL,
            progress DOUBLE PRECISION,
            details_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL
        );
        """
    )
    op.execute(
        "CREATE INDEX ix_run_events_run_seq ON ops.run_events (analysis_run_id, trace_seq);"
    )


def downgrade():
    op.execute("DROP TABLE IF EXISTS ops.run_events CASCADE;")
