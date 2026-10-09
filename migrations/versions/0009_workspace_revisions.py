"""Commit-deferred tenant revisions and bounded history lookup indexes."""

from alembic import op

revision = "0009"
down_revision = "0008"
TABLES = ["runs", "projects", "actions", "approvals", "artifacts", "skill_versions", "memories",
          "evaluation_runs", "evaluation_datasets", "policy_versions", "authorization_epochs", "tool_versions", "budget_accounts"]


def upgrade():
    op.execute("""CREATE TABLE workspace_revisions (
        tenant_id varchar(100) NOT NULL, id varchar(100) NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(), version bigint NOT NULL DEFAULT 0,
        PRIMARY KEY(tenant_id,id))""")
    op.execute("ALTER TABLE workspace_revisions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workspace_revisions FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY tenant_isolation ON workspace_revisions
        USING (tenant_id = current_setting('forge.tenant_id',true))
        WITH CHECK (tenant_id = current_setting('forge.tenant_id',true))""")
    op.execute("""CREATE FUNCTION forge_workspace_changed() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF current_setting('forge.revision_bumped',true) = COALESCE(NEW.tenant_id,OLD.tenant_id) THEN
            RETURN NULL;
        END IF;
        INSERT INTO workspace_revisions(tenant_id,id,version)
            VALUES(COALESCE(NEW.tenant_id,OLD.tenant_id),'workspace',1)
        ON CONFLICT(tenant_id,id) DO UPDATE SET version=workspace_revisions.version+1;
        PERFORM set_config('forge.revision_bumped',COALESCE(NEW.tenant_id,OLD.tenant_id),true);
        RETURN NULL;
    END $$""")
    for table in TABLES:
        op.execute(f"CREATE CONSTRAINT TRIGGER workspace_changed AFTER INSERT OR UPDATE OR DELETE ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION forge_workspace_changed()")
    op.execute("CREATE INDEX checkpoints_rebuild ON checkpoints (tenant_id,run_id,((data->>'event_seq')::bigint) DESC,id DESC) WHERE status='READY' AND data->>'schema_version'='2'")
    op.execute("CREATE INDEX memories_active_project ON memories (tenant_id,(data->>'project')) WHERE status='active'")
    op.execute("CREATE INDEX actions_unknown_age ON actions (tenant_id,created_at,id) WHERE status='UNKNOWN'")


def downgrade():
    for table in TABLES:
        op.execute(f"DROP TRIGGER workspace_changed ON {table}")
    op.execute("DROP FUNCTION forge_workspace_changed()")
    op.execute("DROP INDEX checkpoints_rebuild")
    op.execute("DROP INDEX memories_active_project")
    op.execute("DROP INDEX actions_unknown_age")
    op.execute("DROP TABLE workspace_revisions")
