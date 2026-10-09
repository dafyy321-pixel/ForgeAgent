"""Allow exact redaction of settled privacy lineages without weakening normal immutability."""

from alembic import op

revision = "0006"
down_revision = "0005"


def upgrade():
    op.execute("""CREATE OR REPLACE FUNCTION forge_erasure_authorized(scope text, run_key text) RETURNS boolean LANGUAGE sql AS $$
      SELECT scope = current_setting('forge.tenant_id', true)
        AND EXISTS (SELECT 1 FROM policy_versions p WHERE p.tenant_id = scope
          AND p.id = current_setting('forge.erasure_request', true) AND p.status = 'pending'
          AND (p.data->'runs')::jsonb ? run_key)
        AND EXISTS (SELECT 1 FROM runs r JOIN budget_accounts b ON b.tenant_id=r.tenant_id AND b.id=r.root_id
          WHERE r.tenant_id=scope AND r.id=run_key AND b.reserved=0
            AND COALESCE((b.resources->>'tokens_reserved')::bigint, 0)=0
            AND (r.lease_until IS NULL OR r.lease_until <= clock_timestamp())
            AND NOT EXISTS (SELECT 1 FROM model_calls c JOIN runs child ON child.tenant_id=c.tenant_id AND child.id=c.run_id
              WHERE c.tenant_id=scope AND child.root_id=r.root_id AND c.status IN ('DISPATCHED','UNKNOWN'))
            AND NOT EXISTS (SELECT 1 FROM budget_entries e WHERE e.tenant_id=scope AND e.account_id=r.root_id AND e.status<>'settled')
            AND NOT EXISTS (SELECT 1 FROM actions a JOIN runs child ON child.tenant_id=a.tenant_id AND child.id=a.run_id
              WHERE a.tenant_id=scope AND child.root_id=r.root_id AND a.status IN ('DISPATCHED','RUNNING','UNKNOWN')))
      $$""")
    op.execute("""CREATE OR REPLACE FUNCTION forge_immutable_action() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF NEW.effect_digest <> OLD.effect_digest OR NEW.tool <> OLD.tool
          OR NEW.run_id <> OLD.run_id OR NEW.effect_class <> OLD.effect_class
          THEN RAISE EXCEPTION 'action intent is immutable'; END IF;
        IF NEW.args::text <> OLD.args::text THEN
          IF NEW.args::jsonb = '{}'::jsonb AND (NEW.receipt IS NULL OR NEW.receipt::jsonb = 'null'::jsonb)
            AND forge_erasure_authorized(OLD.tenant_id, OLD.run_id)
            AND EXISTS (SELECT 1 FROM runs r WHERE r.tenant_id=OLD.tenant_id AND r.id=OLD.run_id
              AND r.status='CANCELLED' AND r.state->>'knowledge_erased'='true')
          THEN RETURN NEW; END IF;
          RAISE EXCEPTION 'action intent is immutable';
        END IF;
        RETURN NEW;
      END $$""")
    op.execute("""CREATE OR REPLACE FUNCTION forge_terminal_run() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF OLD.status IN ('SUCCEEDED','FAILED','CANCELLED') AND NEW.status <> OLD.status THEN
          IF NEW.status='CANCELLED' AND NEW.state->>'knowledge_erased'='true'
            AND forge_erasure_authorized(OLD.tenant_id, OLD.id) THEN RETURN NEW; END IF;
          RAISE EXCEPTION 'terminal run is immutable';
        END IF;
        RETURN NEW;
      END $$""")
    op.execute("""CREATE OR REPLACE FUNCTION forge_events_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP='DELETE' AND forge_erasure_authorized(OLD.tenant_id, OLD.run_id)
          AND EXISTS (SELECT 1 FROM runs r WHERE r.tenant_id=OLD.tenant_id AND r.id=OLD.run_id
            AND r.status='CANCELLED' AND r.state->>'knowledge_erased'='true')
        THEN RETURN OLD; END IF;
        RAISE EXCEPTION 'domain events are append only outside registered settled privacy erasures';
      END $$""")


def downgrade():
    raise RuntimeError("Privacy erasure is irreversible; use a reviewed forward migration")
