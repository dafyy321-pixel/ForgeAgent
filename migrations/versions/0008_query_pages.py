"""Indexes for tenant keyset pages and run-scoped console reads."""

from alembic import op

revision = "0008"
down_revision = "0007"
TABLES = ["runs", "projects", "tool_versions", "evaluation_runs", "skill_versions", "memories", "artifacts",
          "approvals", "run_events", "actions", "checkpoints", "policy_versions", "evaluation_datasets"]
RUN_TABLES = ["actions", "artifacts", "run_events", "checkpoints"]


def upgrade():
    for table in TABLES:
        op.execute(f"CREATE INDEX {table}_tenant_page ON {table} (tenant_id,created_at DESC,id DESC)")
    op.execute("CREATE INDEX runs_project_page ON runs (tenant_id,project_id,created_at DESC,id DESC)")
    op.execute("CREATE INDEX runs_status_page ON runs (tenant_id,status,created_at DESC,id DESC)")
    for table in RUN_TABLES:
        op.execute(f"CREATE INDEX {table}_run_page ON {table} (tenant_id,run_id,created_at DESC,id DESC)")


def downgrade():
    for table in RUN_TABLES:
        op.execute(f"DROP INDEX {table}_run_page")
    op.execute("DROP INDEX runs_status_page")
    op.execute("DROP INDEX runs_project_page")
    for table in TABLES:
        op.execute(f"DROP INDEX {table}_tenant_page")
