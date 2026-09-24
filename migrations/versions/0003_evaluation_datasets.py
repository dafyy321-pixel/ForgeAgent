"""Versioned evaluation datasets with tenant isolation."""

from alembic import op
from forgeagent import db

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    db.EvaluationDataset.__table__.create(op.get_bind(), checkfirst=True)
    op.execute("ALTER TABLE evaluation_datasets ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE evaluation_datasets FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON evaluation_datasets")
    op.execute("""CREATE POLICY tenant_isolation ON evaluation_datasets
        USING (tenant_id = current_setting('forge.tenant_id', true))
        WITH CHECK (tenant_id = current_setting('forge.tenant_id', true))""")


def downgrade():
    op.drop_table("evaluation_datasets")
