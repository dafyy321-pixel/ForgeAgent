"""Keep events append-only except for registered, settled privacy erasures."""

from alembic import op

revision = "0005"
down_revision = "0004"


def upgrade():
    op.execute("""CREATE OR REPLACE FUNCTION forge_events_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP = 'DELETE' AND OLD.tenant_id = current_setting('forge.tenant_id', true)
          AND EXISTS (SELECT 1 FROM policy_versions p WHERE p.tenant_id = OLD.tenant_id
            AND p.id = current_setting('forge.erasure_request', true) AND p.status = 'pending'
            AND (p.data->'runs')::jsonb ? OLD.run_id)
          AND EXISTS (SELECT 1 FROM runs r JOIN budget_accounts b
            ON b.tenant_id = r.tenant_id AND b.id = r.root_id
            WHERE r.tenant_id = OLD.tenant_id AND r.id = OLD.run_id
              AND r.status = 'CANCELLED' AND r.state->>'knowledge_erased' = 'true'
              AND b.reserved = 0 AND COALESCE((b.resources->>'tokens_reserved')::bigint, 0) = 0)
        THEN RETURN OLD; END IF;
        RAISE EXCEPTION 'domain events are append only outside registered settled privacy erasures';
      END $$""")


def downgrade():
    raise RuntimeError("Erased records cannot be restored by downgrading; use the reviewed recovery process")
