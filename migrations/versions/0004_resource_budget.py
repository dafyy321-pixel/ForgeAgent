"""Root resource counters for concurrent child runs."""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE budget_accounts ADD COLUMN IF NOT EXISTS resources JSON NOT NULL DEFAULT '{}'")


def downgrade():
    raise RuntimeError("Resource accounting is retained for audit; use an explicit data migration")
