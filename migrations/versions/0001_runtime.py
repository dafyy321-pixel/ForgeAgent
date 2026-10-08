"""Runtime schema and forced tenant isolation."""

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None


def upgrade():
    schema = Path(__file__).with_name("0001_schema.sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(schema)
    # This list is part of the immutable revision, independent of future ORM additions.
    names = [
        "projects", "task_specs", "runs", "run_events", "actions", "approvals", "budget_accounts",
        "budget_entries", "artifacts", "agent_versions", "jobs", "turns", "model_calls", "action_attempts",
        "checkpoints", "semantic_manifests", "context_manifests", "sandboxes", "workspace_snapshots", "tool_versions",
        "skill_versions", "memories", "policy_versions", "authorization_epochs", "verification_results", "outbox",
        "inbox", "evaluation_runs", "evaluation_results",
    ]
    for name in names:
        op.execute(f'ALTER TABLE "{name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{name}" FORCE ROW LEVEL SECURITY')
        op.execute(f'''CREATE POLICY tenant_isolation ON "{name}"
          USING (tenant_id = current_setting('forge.tenant_id', true))
          WITH CHECK (tenant_id = current_setting('forge.tenant_id', true))''')
    op.execute("""CREATE FUNCTION forge_events_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN RAISE EXCEPTION 'domain events are append only'; END $$""")
    op.execute("""CREATE TRIGGER events_immutable BEFORE UPDATE OR DELETE ON run_events
                  FOR EACH ROW EXECUTE FUNCTION forge_events_immutable()""")


def downgrade():
    raise RuntimeError("Destructive downgrade requires a reviewed backup and explicit migration")
