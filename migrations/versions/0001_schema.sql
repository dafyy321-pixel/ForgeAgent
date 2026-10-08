-- Frozen PostgreSQL schema for revision 0001. Never derive historical DDL from live ORM models.

CREATE TABLE agent_versions (
	data JSON NOT NULL,
	status VARCHAR(40) NOT NULL,
	tenant_id VARCHAR(100) NOT NULL,
	id VARCHAR(100) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (tenant_id, id)
);

CREATE TABLE authorization_epochs (
	data JSON NOT NULL,
	status VARCHAR(40) NOT NULL,
	tenant_id VARCHAR(100) NOT NULL,
	id VARCHAR(100) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (tenant_id, id)
);

CREATE TABLE evaluation_runs (
	data JSON NOT NULL,
	status VARCHAR(40) NOT NULL,
	tenant_id VARCHAR(100) NOT NULL,
	id VARCHAR(100) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (tenant_id, id)
);

CREATE TABLE inbox (
	data JSON NOT NULL,
	status VARCHAR(40) NOT NULL,
	tenant_id VARCHAR(100) NOT NULL,
	id VARCHAR(100) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (tenant_id, id)
);

CREATE TABLE memories (
	data JSON NOT NULL,
	status VARCHAR(40) NOT NULL,
	tenant_id VARCHAR(100) NOT NULL,
	id VARCHAR(100) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (tenant_id, id)
);

CREATE TABLE outbox (
	data JSON NOT NULL,
	status VARCHAR(40) NOT NULL,
	tenant_id VARCHAR(100) NOT NULL,
	id VARCHAR(100) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (tenant_id, id)
);

CREATE TABLE policy_versions (
	data JSON NOT NULL,
	status VARCHAR(40) NOT NULL,
	tenant_id VARCHAR(100) NOT NULL,
	id VARCHAR(100) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (tenant_id, id)
);

CREATE TABLE projects (
	data JSON NOT NULL,
	status VARCHAR(40) NOT NULL,
	tenant_id VARCHAR(100) NOT NULL,
	id VARCHAR(100) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (tenant_id, id)
);

CREATE TABLE skill_versions (
	data JSON NOT NULL,
	status VARCHAR(40) NOT NULL,
	tenant_id VARCHAR(100) NOT NULL,
	id VARCHAR(100) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (tenant_id, id)
);

CREATE TABLE task_specs (
	data JSON NOT NULL,
	status VARCHAR(40) NOT NULL,
	tenant_id VARCHAR(100) NOT NULL,
	id VARCHAR(100) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (tenant_id, id)
);

CREATE TABLE tenants (
	id VARCHAR(100) NOT NULL,
	PRIMARY KEY (id)
);

CREATE TABLE tool_versions (
	data JSON NOT NULL,
	status VARCHAR(40) NOT NULL,
	tenant_id VARCHAR(100) NOT NULL,
	id VARCHAR(100) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (tenant_id, id)
);

CREATE TABLE runs (
	project_id VARCHAR(100) NOT NULL,
	task_id VARCHAR(100) NOT NULL,
	root_id VARCHAR(100) NOT NULL,
	parent_id VARCHAR(100),
	request_key VARCHAR(200) NOT NULL,
	request_digest VARCHAR(80) NOT NULL,
	actor VARCHAR(200) NOT NULL,
	status VARCHAR(30) NOT NULL,
	phase VARCHAR(40) NOT NULL,
	wait_reason VARCHAR(40),
	version INTEGER NOT NULL,
	seq INTEGER NOT NULL,
	epoch INTEGER NOT NULL,
	lease_owner VARCHAR(100),
	lease_until TIMESTAMP WITH TIME ZONE,
	available_at TIMESTAMP WITH TIME ZONE NOT NULL,
	cancel_requested BOOLEAN NOT NULL,
	pause_requested BOOLEAN NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	state JSON NOT NULL,
	tenant_id VARCHAR(100) NOT NULL,
	id VARCHAR(100) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (tenant_id, id),
	FOREIGN KEY(tenant_id, project_id) REFERENCES projects (tenant_id, id),
	FOREIGN KEY(tenant_id, task_id) REFERENCES task_specs (tenant_id, id),
	FOREIGN KEY(tenant_id, root_id) REFERENCES runs (tenant_id, id),
	FOREIGN KEY(tenant_id, parent_id) REFERENCES runs (tenant_id, id),
	UNIQUE (tenant_id, request_key),
	CHECK (status IN ('QUEUED','ACTIVE','WAITING','PAUSED','CANCELLING','SUCCEEDED','FAILED','CANCELLED'))
);

CREATE INDEX ix_runs_schedule ON runs (tenant_id, status, available_at);

CREATE TABLE action_attempts (
	run_id VARCHAR(100),
	data JSON NOT NULL,
	status VARCHAR(40) NOT NULL,
	tenant_id VARCHAR(100) NOT NULL,
	id VARCHAR(100) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (tenant_id, id),
	FOREIGN KEY(tenant_id, run_id) REFERENCES runs (tenant_id, id)
);

CREATE INDEX ix_action_attempts_run_id ON action_attempts (run_id);

CREATE TABLE actions (
	logical_key VARCHAR(100) NOT NULL,
	tool VARCHAR(200) NOT NULL,
	args JSON NOT NULL,
	effect_class VARCHAR(50) NOT NULL,
	effect_digest VARCHAR(80) NOT NULL,
	status VARCHAR(40) NOT NULL,
	attempt INTEGER NOT NULL,
	epoch INTEGER NOT NULL,
	receipt JSON,
	consumed BOOLEAN NOT NULL,
	run_id VARCHAR(100) NOT NULL,
	tenant_id VARCHAR(100) NOT NULL,
	id VARCHAR(100) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (tenant_id, id),
	UNIQUE (tenant_id, run_id, logical_key),
	FOREIGN KEY(tenant_id, run_id) REFERENCES runs (tenant_id, id)
);

CREATE INDEX ix_actions_run_id ON actions (run_id);

CREATE TABLE artifacts (
	name VARCHAR(200) NOT NULL,
	kind VARCHAR(40) NOT NULL,
	ref JSON NOT NULL,
	verified BOOLEAN NOT NULL,
	version INTEGER NOT NULL,
	run_id VARCHAR(100) NOT NULL,
	tenant_id VARCHAR(100) NOT NULL,
	id VARCHAR(100) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (tenant_id, id),
	FOREIGN KEY(tenant_id, run_id) REFERENCES runs (tenant_id, id)
);

CREATE INDEX ix_artifacts_run_id ON artifacts (run_id);

CREATE TABLE budget_accounts (
	limit_micros BIGINT NOT NULL,
	spent BIGINT NOT NULL,
	reserved BIGINT NOT NULL,
	tenant_id VARCHAR(100) NOT NULL,
	id VARCHAR(100) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (tenant_id, id),
	FOREIGN KEY(tenant_id, id) REFERENCES runs (tenant_id, id),
	CHECK (spent >= 0 AND reserved >= 0 AND limit_micros >= 0)
);

CREATE TABLE checkpoints (
	run_id VARCHAR(100),
	data JSON NOT NULL,
	status VARCHAR(40) NOT NULL,
	tenant_id VARCHAR(100) NOT NULL,
	id VARCHAR(100) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (tenant_id, id),
	FOREIGN KEY(tenant_id, run_id) REFERENCES runs (tenant_id, id)
);

CREATE INDEX ix_checkpoints_run_id ON checkpoints (run_id);

CREATE TABLE context_manifests (
	run_id VARCHAR(100),
	data JSON NOT NULL,
	status VARCHAR(40) NOT NULL,
	tenant_id VARCHAR(100) NOT NULL,
	id VARCHAR(100) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (tenant_id, id),
	FOREIGN KEY(tenant_id, run_id) REFERENCES runs (tenant_id, id)
);

CREATE INDEX ix_context_manifests_run_id ON context_manifests (run_id);

CREATE TABLE evaluation_results (
	run_id VARCHAR(100),
	data JSON NOT NULL,
	status VARCHAR(40) NOT NULL,
	tenant_id VARCHAR(100) NOT NULL,
	id VARCHAR(100) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (tenant_id, id),
	FOREIGN KEY(tenant_id, run_id) REFERENCES runs (tenant_id, id)
);

CREATE INDEX ix_evaluation_results_run_id ON evaluation_results (run_id);

CREATE TABLE jobs (
	run_id VARCHAR(100),
	data JSON NOT NULL,
	status VARCHAR(40) NOT NULL,
	tenant_id VARCHAR(100) NOT NULL,
	id VARCHAR(100) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (tenant_id, id),
	FOREIGN KEY(tenant_id, run_id) REFERENCES runs (tenant_id, id)
);

CREATE INDEX ix_jobs_run_id ON jobs (run_id);

CREATE TABLE model_calls (
	run_id VARCHAR(100),
	data JSON NOT NULL,
	status VARCHAR(40) NOT NULL,
	tenant_id VARCHAR(100) NOT NULL,
	id VARCHAR(100) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (tenant_id, id),
	FOREIGN KEY(tenant_id, run_id) REFERENCES runs (tenant_id, id)
);

CREATE INDEX ix_model_calls_run_id ON model_calls (run_id);

CREATE TABLE run_events (
	seq INTEGER NOT NULL,
	type VARCHAR(80) NOT NULL,
	payload JSON NOT NULL,
	run_id VARCHAR(100) NOT NULL,
	tenant_id VARCHAR(100) NOT NULL,
	id VARCHAR(100) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (tenant_id, id),
	UNIQUE (tenant_id, run_id, seq),
	FOREIGN KEY(tenant_id, run_id) REFERENCES runs (tenant_id, id)
);

CREATE INDEX ix_run_events_run_id ON run_events (run_id);

CREATE TABLE sandboxes (
	run_id VARCHAR(100),
	data JSON NOT NULL,
	status VARCHAR(40) NOT NULL,
	tenant_id VARCHAR(100) NOT NULL,
	id VARCHAR(100) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (tenant_id, id),
	FOREIGN KEY(tenant_id, run_id) REFERENCES runs (tenant_id, id)
);

CREATE INDEX ix_sandboxes_run_id ON sandboxes (run_id);

CREATE TABLE semantic_manifests (
	run_id VARCHAR(100),
	data JSON NOT NULL,
	status VARCHAR(40) NOT NULL,
	tenant_id VARCHAR(100) NOT NULL,
	id VARCHAR(100) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (tenant_id, id),
	FOREIGN KEY(tenant_id, run_id) REFERENCES runs (tenant_id, id)
);

CREATE INDEX ix_semantic_manifests_run_id ON semantic_manifests (run_id);

CREATE TABLE turns (
	run_id VARCHAR(100),
	data JSON NOT NULL,
	status VARCHAR(40) NOT NULL,
	tenant_id VARCHAR(100) NOT NULL,
	id VARCHAR(100) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (tenant_id, id),
	FOREIGN KEY(tenant_id, run_id) REFERENCES runs (tenant_id, id)
);

CREATE INDEX ix_turns_run_id ON turns (run_id);

CREATE TABLE verification_results (
	run_id VARCHAR(100),
	data JSON NOT NULL,
	status VARCHAR(40) NOT NULL,
	tenant_id VARCHAR(100) NOT NULL,
	id VARCHAR(100) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (tenant_id, id),
	FOREIGN KEY(tenant_id, run_id) REFERENCES runs (tenant_id, id)
);

CREATE INDEX ix_verification_results_run_id ON verification_results (run_id);

CREATE TABLE workspace_snapshots (
	run_id VARCHAR(100),
	data JSON NOT NULL,
	status VARCHAR(40) NOT NULL,
	tenant_id VARCHAR(100) NOT NULL,
	id VARCHAR(100) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (tenant_id, id),
	FOREIGN KEY(tenant_id, run_id) REFERENCES runs (tenant_id, id)
);

CREATE INDEX ix_workspace_snapshots_run_id ON workspace_snapshots (run_id);

CREATE TABLE approvals (
	action_id VARCHAR(100) NOT NULL,
	effect_digest VARCHAR(80) NOT NULL,
	policy_epoch INTEGER NOT NULL,
	resource_version VARCHAR(100) NOT NULL,
	decision VARCHAR(20) NOT NULL,
	reviewer VARCHAR(200),
	reason TEXT NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	tenant_id VARCHAR(100) NOT NULL,
	id VARCHAR(100) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (tenant_id, id),
	FOREIGN KEY(tenant_id, action_id) REFERENCES actions (tenant_id, id)
);

CREATE TABLE budget_entries (
	account_id VARCHAR(100) NOT NULL,
	operation_id VARCHAR(100) NOT NULL,
	reserved BIGINT NOT NULL,
	actual BIGINT,
	data JSON NOT NULL,
	status VARCHAR(40) NOT NULL,
	tenant_id VARCHAR(100) NOT NULL,
	id VARCHAR(100) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (tenant_id, id),
	UNIQUE (tenant_id, operation_id),
	FOREIGN KEY(tenant_id, account_id) REFERENCES budget_accounts (tenant_id, id)
);
