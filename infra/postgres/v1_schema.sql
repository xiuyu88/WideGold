CREATE TABLE decision.asset_scores (
	asset_score_id UUID NOT NULL, 
	analysis_run_id UUID NOT NULL, 
	asset_id VARCHAR(64) NOT NULL, 
	as_of_ts TIMESTAMP WITH TIME ZONE NOT NULL, 
	tactical_score FLOAT NOT NULL, 
	swing_score FLOAT NOT NULL, 
	strategic_score FLOAT NOT NULL, 
	direction_score FLOAT NOT NULL, 
	asset_score FLOAT NOT NULL, 
	label VARCHAR(32) NOT NULL, 
	confidence FLOAT NOT NULL, 
	coverage FLOAT NOT NULL, 
	published BOOLEAN NOT NULL, 
	supersedes_score_id UUID, 
	version_snapshot JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	published_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (asset_score_id)
);

CREATE INDEX ix_asset_scores_asset_published ON decision.asset_scores (asset_id, published, as_of_ts);

CREATE TABLE decision.factor_states (
	factor_state_id UUID NOT NULL, 
	analysis_run_id UUID NOT NULL, 
	factor_id VARCHAR(64) NOT NULL, 
	as_of_ts TIMESTAMP WITH TIME ZONE NOT NULL, 
	state FLOAT NOT NULL, 
	reliability FLOAT NOT NULL, 
	coverage FLOAT NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	components JSONB NOT NULL, 
	evidence_refs JSONB NOT NULL, 
	event_ids JSONB NOT NULL, 
	logic_version VARCHAR(32) NOT NULL, 
	data_vintage VARCHAR(64), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (factor_state_id)
);

CREATE INDEX ix_factor_states_lookup ON decision.factor_states (factor_id, as_of_ts);

CREATE TABLE decision.reports (
	report_id UUID NOT NULL, 
	analysis_run_id UUID NOT NULL, 
	report_type VARCHAR(32) NOT NULL, 
	asset_id VARCHAR(64), 
	title TEXT NOT NULL, 
	summary TEXT NOT NULL, 
	body_markdown TEXT NOT NULL, 
	prompt_version VARCHAR(32) NOT NULL, 
	published BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (report_id)
);

CREATE TABLE iam.roles (
	role_code VARCHAR(32) NOT NULL, 
	name VARCHAR(64) NOT NULL, 
	PRIMARY KEY (role_code)
);

CREATE TABLE iam.users (
	user_id UUID NOT NULL, 
	username VARCHAR(64) NOT NULL, 
	password_hash TEXT NOT NULL, 
	display_name VARCHAR(128), 
	email VARCHAR(255), 
	is_active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	last_login_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (user_id), 
	CONSTRAINT uq_users_username UNIQUE (username)
);

CREATE TABLE intel.events (
	event_id UUID NOT NULL, 
	cluster_id UUID, 
	analysis_run_id UUID NOT NULL, 
	event_type VARCHAR(64) NOT NULL, 
	canonical_title TEXT NOT NULL, 
	published_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	effective_at TIMESTAMP WITH TIME ZONE, 
	ingest_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	verification_status VARCHAR(32) NOT NULL, 
	source_tier VARCHAR(8) NOT NULL, 
	strength SMALLINT NOT NULL, 
	confidence FLOAT NOT NULL, 
	novelty FLOAT NOT NULL, 
	priced_in FLOAT NOT NULL, 
	implementation FLOAT NOT NULL, 
	horizon VARCHAR(16) NOT NULL, 
	half_life_days FLOAT NOT NULL, 
	parent_event_id UUID, 
	graph_version VARCHAR(32) NOT NULL, 
	prompt_version VARCHAR(32) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	raw_json JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (event_id)
);

CREATE INDEX ix_events_run_published ON intel.events (analysis_run_id, published_at);

CREATE TABLE intel.llm_runs (
	llm_run_id UUID NOT NULL, 
	analysis_run_id UUID, 
	graph_name VARCHAR(64), 
	node_name VARCHAR(64), 
	task_type VARCHAR(32) NOT NULL, 
	model_tier VARCHAR(8) NOT NULL, 
	provider VARCHAR(64) NOT NULL, 
	model_alias VARCHAR(64) NOT NULL, 
	resolved_model VARCHAR(128) NOT NULL, 
	reasoning_effort VARCHAR(16) NOT NULL, 
	request_id VARCHAR(128), 
	started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	finished_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	latency_ms INTEGER NOT NULL, 
	input_tokens INTEGER, 
	output_tokens INTEGER, 
	schema_valid BOOLEAN NOT NULL, 
	retry_count INTEGER NOT NULL, 
	fallback_from VARCHAR(64), 
	status VARCHAR(16) NOT NULL, 
	error_code VARCHAR(64), 
	error_message TEXT, 
	metadata_json JSONB NOT NULL, 
	PRIMARY KEY (llm_run_id)
);

CREATE INDEX ix_llm_runs_analysis ON intel.llm_runs (analysis_run_id);

CREATE TABLE market.factor_inputs (
	factor_input_id UUID NOT NULL, 
	analysis_run_id UUID, 
	factor_id VARCHAR(64) NOT NULL, 
	value FLOAT, 
	observed_at TIMESTAMP WITH TIME ZONE, 
	status VARCHAR(16) NOT NULL, 
	reliability FLOAT NOT NULL, 
	source_ids JSONB NOT NULL, 
	warnings JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (factor_input_id)
);

CREATE INDEX ix_factor_inputs_lookup ON market.factor_inputs (factor_id, observed_at);

CREATE TABLE market.macro_observations (
	observation_id UUID NOT NULL, 
	series_id VARCHAR(64) NOT NULL, 
	observation_date DATE NOT NULL, 
	release_ts TIMESTAMP WITH TIME ZONE NOT NULL, 
	ingest_ts TIMESTAMP WITH TIME ZONE NOT NULL, 
	value FLOAT NOT NULL, 
	revision_vintage VARCHAR(64) NOT NULL, 
	definition_version VARCHAR(64) NOT NULL, 
	is_preliminary BOOLEAN NOT NULL, 
	source_id VARCHAR(64) NOT NULL, 
	metadata_json JSONB NOT NULL, 
	PRIMARY KEY (observation_id)
);

CREATE INDEX ix_macro_series_release ON market.macro_observations (series_id, release_ts);

CREATE INDEX ix_macro_series_observation ON market.macro_observations (series_id, observation_date);

CREATE TABLE market.prices_daily (
	instrument_id VARCHAR(64) NOT NULL, 
	trade_date DATE NOT NULL, 
	open FLOAT, 
	high FLOAT, 
	low FLOAT, 
	close FLOAT NOT NULL, 
	volume FLOAT, 
	turnover FLOAT, 
	source_id VARCHAR(64) NOT NULL, 
	ingest_ts TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_prices_daily PRIMARY KEY (instrument_id, trade_date, source_id)
);

CREATE TABLE ops.analysis_runs (
	analysis_run_id UUID NOT NULL, 
	prefect_flow_run_id UUID, 
	analysis_date DATE NOT NULL, 
	as_of_ts TIMESTAMP WITH TIME ZONE NOT NULL, 
	data_cutoff_ts TIMESTAMP WITH TIME ZONE NOT NULL, 
	trigger_type VARCHAR(32) NOT NULL, 
	run_mode VARCHAR(32) NOT NULL, 
	publish_mode VARCHAR(32) NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	requested_by UUID, 
	base_run_id UUID, 
	version_snapshot JSONB NOT NULL, 
	coverage FLOAT, 
	quality_gate JSONB, 
	started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	error_code VARCHAR(64), 
	error_summary TEXT, 
	PRIMARY KEY (analysis_run_id)
);

CREATE INDEX ix_analysis_runs_date_status ON ops.analysis_runs (analysis_date, status);

CREATE TABLE ops.analysis_snapshots (
	analysis_run_id UUID NOT NULL, 
	snapshot_json JSONB NOT NULL, 
	published BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	published_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (analysis_run_id)
);

CREATE INDEX ix_analysis_snapshots_published ON ops.analysis_snapshots (published, created_at);

CREATE TABLE ops.audit_logs (
	audit_id UUID NOT NULL, 
	actor_user_id UUID, 
	action VARCHAR(64) NOT NULL, 
	resource_type VARCHAR(64) NOT NULL, 
	resource_id VARCHAR(128) NOT NULL, 
	before_json JSONB, 
	after_json JSONB, 
	request_id VARCHAR(128), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (audit_id)
);

CREATE INDEX ix_audit_resource ON ops.audit_logs (resource_type, resource_id);

CREATE TABLE ops.data_fetch_runs (
	fetch_run_id UUID NOT NULL, 
	analysis_run_id UUID NOT NULL, 
	dataset VARCHAR(64) NOT NULL, 
	provider VARCHAR(64), 
	started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	finished_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	rows_count INTEGER NOT NULL, 
	cache_hit BOOLEAN NOT NULL, 
	fallback_used BOOLEAN NOT NULL, 
	error_code VARCHAR(64), 
	error_message TEXT, 
	PRIMARY KEY (fetch_run_id)
);

CREATE TABLE raw.documents (
	document_id UUID NOT NULL, 
	source_id VARCHAR(64) NOT NULL, 
	source_url TEXT, 
	document_type VARCHAR(32) NOT NULL, 
	title TEXT NOT NULL, 
	raw_text TEXT NOT NULL, 
	language VARCHAR(16) NOT NULL, 
	published_at TIMESTAMP WITH TIME ZONE, 
	retrieved_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	content_hash VARCHAR(128) NOT NULL, 
	raw_metadata JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (document_id), 
	CONSTRAINT uq_document_source_hash UNIQUE (source_id, content_hash)
);

CREATE INDEX ix_raw_documents_published ON raw.documents (published_at);

CREATE TABLE reference.assets (
	asset_id VARCHAR(64) NOT NULL, 
	name VARCHAR(128) NOT NULL, 
	asset_class VARCHAR(32) NOT NULL, 
	currency VARCHAR(16) NOT NULL, 
	index_code VARCHAR(32), 
	exchange VARCHAR(32), 
	active BOOLEAN NOT NULL, 
	display_order INTEGER NOT NULL, 
	metadata_json JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (asset_id)
);

CREATE TABLE reference.config_versions (
	config_version_id UUID NOT NULL, 
	config_type VARCHAR(32) NOT NULL, 
	version VARCHAR(32) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	content_hash VARCHAR(128) NOT NULL, 
	content_json JSONB NOT NULL, 
	effective_from TIMESTAMP WITH TIME ZONE NOT NULL, 
	effective_to TIMESTAMP WITH TIME ZONE, 
	created_by UUID, 
	approved_by UUID, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	approved_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (config_version_id), 
	CONSTRAINT uq_config_type_version UNIQUE (config_type, version)
);

CREATE TABLE reference.data_sources (
	source_id VARCHAR(64) NOT NULL, 
	name VARCHAR(128) NOT NULL, 
	source_type VARCHAR(32) NOT NULL, 
	source_tier VARCHAR(8) NOT NULL, 
	provider_key VARCHAR(64) NOT NULL, 
	base_url TEXT, 
	priority INTEGER NOT NULL, 
	active BOOLEAN NOT NULL, 
	license_notes TEXT, 
	metadata_json JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (source_id)
);

CREATE TABLE reference.factor_definitions (
	factor_id VARCHAR(64) NOT NULL, 
	name VARCHAR(128) NOT NULL, 
	family VARCHAR(32) NOT NULL, 
	category VARCHAR(64), 
	semantic_positive TEXT, 
	semantic_negative TEXT, 
	evidence_grade VARCHAR(8) NOT NULL, 
	lifecycle VARCHAR(16) NOT NULL, 
	correlation_group VARCHAR(64), 
	update_frequency VARCHAR(32), 
	max_age_seconds INTEGER, 
	logic_version VARCHAR(32) NOT NULL, 
	production_enabled BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (factor_id)
);

CREATE TABLE research.approval_records (
	approval_id UUID NOT NULL, 
	candidate_id UUID NOT NULL, 
	action VARCHAR(32) NOT NULL, 
	reviewer_id UUID, 
	comment TEXT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (approval_id)
);

CREATE TABLE research.evidence_items (
	evidence_id UUID NOT NULL, 
	research_request_id UUID NOT NULL, 
	source_type VARCHAR(32) NOT NULL, 
	source_tier VARCHAR(8) NOT NULL, 
	title TEXT NOT NULL, 
	url TEXT, 
	published_at TIMESTAMP WITH TIME ZONE, 
	retrieved_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	supports BOOLEAN, 
	claim_summary TEXT NOT NULL, 
	evidence_summary TEXT NOT NULL, 
	quality_score FLOAT NOT NULL, 
	metadata_json JSONB NOT NULL, 
	PRIMARY KEY (evidence_id)
);

CREATE TABLE research.factor_candidates (
	candidate_id UUID NOT NULL, 
	research_request_id UUID NOT NULL, 
	target_factor_id VARCHAR(64), 
	candidate_type VARCHAR(32) NOT NULL, 
	hypothesis TEXT NOT NULL, 
	mechanism TEXT NOT NULL, 
	evidence_grade VARCHAR(8) NOT NULL, 
	proposed_config_patch JSONB NOT NULL, 
	validation_required JSONB NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	production_enabled BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (candidate_id)
);

CREATE TABLE research.requests (
	research_request_id UUID NOT NULL, 
	request_type VARCHAR(32) NOT NULL, 
	target_factor_id VARCHAR(64), 
	title TEXT NOT NULL, 
	question TEXT NOT NULL, 
	requested_by UUID, 
	status VARCHAR(16) NOT NULL, 
	prefect_flow_run_id UUID, 
	langgraph_thread_id VARCHAR(128), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (research_request_id)
);

CREATE TABLE scenario.runs (
	scenario_run_id UUID NOT NULL, 
	user_id UUID, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	assumption_text TEXT NOT NULL, 
	base_analysis_run_id UUID, 
	status VARCHAR(16) NOT NULL, 
	graph_thread_id VARCHAR(128), 
	result_json JSONB NOT NULL, 
	PRIMARY KEY (scenario_run_id)
);

CREATE TABLE decision.score_contributions (
	contribution_id UUID NOT NULL, 
	asset_score_id UUID NOT NULL, 
	factor_id VARCHAR(64) NOT NULL, 
	factor_state FLOAT NOT NULL, 
	sensitivity FLOAT NOT NULL, 
	reliability FLOAT NOT NULL, 
	raw_contribution FLOAT NOT NULL, 
	after_conflict FLOAT NOT NULL, 
	after_group_cap FLOAT NOT NULL, 
	rank_positive INTEGER, 
	rank_negative INTEGER, 
	PRIMARY KEY (contribution_id), 
	FOREIGN KEY(asset_score_id) REFERENCES decision.asset_scores (asset_score_id)
);

CREATE TABLE iam.user_roles (
	user_id UUID NOT NULL, 
	role_code VARCHAR(32) NOT NULL, 
	CONSTRAINT pk_user_roles PRIMARY KEY (user_id, role_code), 
	FOREIGN KEY(user_id) REFERENCES iam.users (user_id), 
	FOREIGN KEY(role_code) REFERENCES iam.roles (role_code)
);

CREATE TABLE intel.event_factor_links (
	link_id UUID NOT NULL, 
	event_id UUID NOT NULL, 
	factor_id VARCHAR(64) NOT NULL, 
	direction SMALLINT NOT NULL, 
	mapping_confidence FLOAT NOT NULL, 
	reason_tags JSONB NOT NULL, 
	asset_override_json JSONB, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (link_id), 
	FOREIGN KEY(event_id) REFERENCES intel.events (event_id)
);

CREATE TABLE reference.asset_factor_weights (
	weight_id UUID NOT NULL, 
	asset_id VARCHAR(64) NOT NULL, 
	factor_id VARCHAR(64) NOT NULL, 
	horizon VARCHAR(16) NOT NULL, 
	sensitivity FLOAT NOT NULL, 
	max_contribution FLOAT, 
	weight_version VARCHAR(32) NOT NULL, 
	effective_from TIMESTAMP WITH TIME ZONE NOT NULL, 
	effective_to TIMESTAMP WITH TIME ZONE, 
	rationale TEXT, 
	approved_by UUID, 
	approved_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (weight_id), 
	FOREIGN KEY(asset_id) REFERENCES reference.assets (asset_id), 
	FOREIGN KEY(factor_id) REFERENCES reference.factor_definitions (factor_id)
);

CREATE INDEX ix_asset_factor_weights_lookup ON reference.asset_factor_weights (asset_id, factor_id, horizon, effective_from);
