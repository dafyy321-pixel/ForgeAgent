"""Runtime schema and forced tenant isolation."""

from alembic import op
from forgeagent.db import Base

revision = "0001"
down_revision = None


def upgrade():
    Base.metadata.create_all(op.get_bind())
    for table in Base.metadata.sorted_tables:
        if "tenant_id" not in table.c:
            continue
        name = table.name
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
