"""Indicator registry, point-in-time observations, provider health, scoped factor states.

Revision ID: 0004
Revises: 0003
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''
    CREATE TABLE reference.indicator_definitions (
      indicator_id VARCHAR(64) PRIMARY KEY,
      name VARCHAR(128) NOT NULL,
      provider VARCHAR(32) NOT NULL,
      source_id VARCHAR(64) NOT NULL,
      frequency VARCHAR(32) NOT NULL,
      definition_version VARCHAR(32) NOT NULL,
      metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
      active BOOLEAN NOT NULL DEFAULT TRUE,
      created_at TIMESTAMPTZ NOT NULL,
      updated_at TIMESTAMPTZ NOT NULL
    );
    CREATE TABLE market.indicator_observations (
      indicator_observation_id UUID PRIMARY KEY,
      indicator_id VARCHAR(64) NOT NULL,
      asset_id VARCHAR(64),
      observation_date DATE NOT NULL,
      release_ts TIMESTAMPTZ NOT NULL,
      ingest_ts TIMESTAMPTZ NOT NULL,
      value DOUBLE PRECISION NOT NULL,
      source_id VARCHAR(64) NOT NULL,
      revision_vintage VARCHAR(64) NOT NULL,
      definition_version VARCHAR(64) NOT NULL,
      status VARCHAR(16) NOT NULL,
      metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
      CONSTRAINT uq_indicator_vintage UNIQUE NULLS NOT DISTINCT(indicator_id, asset_id, observation_date, release_ts, source_id)
    );
    CREATE INDEX ix_indicator_obs_lookup ON market.indicator_observations(indicator_id, asset_id, observation_date, release_ts);
    CREATE TABLE ops.provider_health (
      provider_key VARCHAR(64) PRIMARY KEY,
      checked_at TIMESTAMPTZ NOT NULL,
      status VARCHAR(16) NOT NULL,
      latency_ms INTEGER,
      last_success_at TIMESTAMPTZ,
      consecutive_failures INTEGER NOT NULL DEFAULT 0,
      details_json JSONB NOT NULL DEFAULT '{}'::jsonb
    );
    ALTER TABLE market.factor_inputs ADD COLUMN asset_id VARCHAR(64);
    ALTER TABLE decision.factor_states ADD COLUMN asset_id VARCHAR(64);
    CREATE INDEX ix_factor_inputs_scoped ON market.factor_inputs(factor_id, asset_id, observed_at);
    CREATE INDEX ix_factor_states_scoped ON decision.factor_states(factor_id, asset_id, as_of_ts);
    ''')


def downgrade():
    op.execute('''
    DROP INDEX IF EXISTS decision.ix_factor_states_scoped;
    DROP INDEX IF EXISTS market.ix_factor_inputs_scoped;
    ALTER TABLE decision.factor_states DROP COLUMN IF EXISTS asset_id;
    ALTER TABLE market.factor_inputs DROP COLUMN IF EXISTS asset_id;
    DROP TABLE IF EXISTS ops.provider_health CASCADE;
    DROP TABLE IF EXISTS market.indicator_observations CASCADE;
    DROP TABLE IF EXISTS reference.indicator_definitions CASCADE;
    ''')
