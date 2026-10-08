"""Versioned evaluation datasets with tenant isolation."""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS evaluation_datasets (
        data JSON NOT NULL,
        status VARCHAR(40) NOT NULL,
        tenant_id VARCHAR(100) NOT NULL,
        id VARCHAR(100) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (tenant_id, id)
    )""")
    op.execute("ALTER TABLE evaluation_datasets ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE evaluation_datasets FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON evaluation_datasets")
    op.execute("""CREATE POLICY tenant_isolation ON evaluation_datasets
        USING (tenant_id = current_setting('forge.tenant_id', true))
        WITH CHECK (tenant_id = current_setting('forge.tenant_id', true))""")


def downgrade():
    op.drop_table("evaluation_datasets")
