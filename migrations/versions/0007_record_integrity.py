"""Enforce journal/evidence identity and same-tenant JSON relationships."""

from alembic import op

revision = "0007"
down_revision = "0006"


def upgrade():
    op.execute("""CREATE OR REPLACE FUNCTION forge_immutable_action() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE redaction boolean;
      BEGIN
        IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR NEW.id IS DISTINCT FROM OLD.id
          OR NEW.logical_key IS DISTINCT FROM OLD.logical_key OR NEW.effect_digest IS DISTINCT FROM OLD.effect_digest
          OR NEW.tool IS DISTINCT FROM OLD.tool OR NEW.run_id IS DISTINCT FROM OLD.run_id
          OR NEW.effect_class IS DISTINCT FROM OLD.effect_class THEN RAISE EXCEPTION 'action intent is immutable'; END IF;
        redaction := NEW.args::jsonb='{}'::jsonb AND (NEW.receipt IS NULL OR NEW.receipt::jsonb='null'::jsonb)
          AND forge_erasure_authorized(OLD.tenant_id, OLD.run_id)
          AND EXISTS (SELECT 1 FROM runs r WHERE r.tenant_id=OLD.tenant_id AND r.id=OLD.run_id
            AND r.status='CANCELLED' AND r.state->>'knowledge_erased'='true');
        IF NEW.args::jsonb IS DISTINCT FROM OLD.args::jsonb AND NOT redaction
          THEN RAISE EXCEPTION 'action intent is immutable'; END IF;
        IF OLD.status IN ('SUCCEEDED','FAILED','CANCELLED') AND (NEW.status IS DISTINCT FROM OLD.status
          OR (NEW.receipt::jsonb IS DISTINCT FROM OLD.receipt::jsonb AND NOT redaction))
          THEN RAISE EXCEPTION 'settled action receipt and state are immutable'; END IF;
        RETURN NEW;
      END $$""")
    op.execute("""CREATE FUNCTION forge_record_integrity() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE linked text;
      BEGIN
        IF TG_OP='DELETE' THEN
          IF forge_erasure_authorized(OLD.tenant_id, OLD.run_id)
            AND EXISTS (SELECT 1 FROM runs r WHERE r.tenant_id=OLD.tenant_id AND r.id=OLD.run_id
              AND r.state->>'knowledge_erased'='true') THEN RETURN OLD; END IF;
          RAISE EXCEPTION 'durable evidence deletion requires registered settled erasure';
        END IF;
        IF TG_OP='UPDATE' AND (NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR NEW.id IS DISTINCT FROM OLD.id
          OR NEW.run_id IS DISTINCT FROM OLD.run_id) THEN RAISE EXCEPTION 'durable record identity is immutable'; END IF;
        IF TG_TABLE_NAME IN ('turns','checkpoints','semantic_manifests','context_manifests','verification_results')
          AND TG_OP='UPDATE' AND NEW.data::jsonb IS DISTINCT FROM OLD.data::jsonb
          THEN RAISE EXCEPTION 'committed evidence is immutable'; END IF;
        IF TG_TABLE_NAME='action_attempts' THEN
          IF TG_OP='UPDATE' AND ((NEW.data->'action_id')::jsonb IS DISTINCT FROM (OLD.data->'action_id')::jsonb
            OR (NEW.data->'number')::jsonb IS DISTINCT FROM (OLD.data->'number')::jsonb
            OR (NEW.data->'epoch')::jsonb IS DISTINCT FROM (OLD.data->'epoch')::jsonb)
            THEN RAISE EXCEPTION 'attempt identity is immutable'; END IF;
          linked := NEW.data->>'action_id';
          IF linked IS NULL OR NOT EXISTS (SELECT 1 FROM actions a WHERE a.tenant_id=NEW.tenant_id AND a.id=linked AND a.run_id=NEW.run_id)
            THEN RAISE EXCEPTION 'attempt action must belong to the same run and tenant'; END IF;
        END IF;
        IF TG_TABLE_NAME='turns' THEN
          linked := NEW.data->>'call_id';
          IF linked IS NULL OR NOT EXISTS (SELECT 1 FROM model_calls c WHERE c.tenant_id=NEW.tenant_id AND c.id=linked AND c.run_id=NEW.run_id)
            THEN RAISE EXCEPTION 'turn model call must belong to the same run and tenant'; END IF;
        END IF;
        RETURN NEW;
      END $$""")
    for table in ["turns", "action_attempts", "checkpoints", "semantic_manifests", "context_manifests", "verification_results"]:
        op.execute(f"CREATE TRIGGER durable_record BEFORE INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION forge_record_integrity()")
    op.execute("""CREATE FUNCTION forge_model_identity() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE field text;
      BEGIN
        IF TG_OP='UPDATE' THEN
          IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR NEW.id IS DISTINCT FROM OLD.id OR NEW.run_id IS DISTINCT FROM OLD.run_id
            THEN RAISE EXCEPTION 'model operation identity is immutable'; END IF;
          IF forge_erasure_authorized(OLD.tenant_id, OLD.run_id) AND NEW.data->>'erased'='true'
            AND NEW.data::jsonb - ARRAY['erased','usage','request_digest'] = '{}'::jsonb
            AND (NEW.data->'request_digest')::jsonb IS NOT DISTINCT FROM COALESCE((OLD.data->'request_digest')::jsonb,'null'::jsonb)
            AND (NEW.data->'usage')::jsonb IS NOT DISTINCT FROM COALESCE((OLD.data->'usage')::jsonb,'null'::jsonb)
            THEN RETURN NEW; END IF;
          FOREACH field IN ARRAY ARRAY['request_digest','ordinal','input_revision','context_digest','model_id','messages_ref','rate_card'] LOOP
            IF (NEW.data->field)::jsonb IS DISTINCT FROM (OLD.data->field)::jsonb THEN RAISE EXCEPTION 'model request identity is immutable'; END IF;
          END LOOP;
          FOREACH field IN ARRAY ARRAY['response_ref','decision','protocol_receipt'] LOOP
            IF OLD.data::jsonb ? field AND (NEW.data->field)::jsonb IS DISTINCT FROM (OLD.data->field)::jsonb
              THEN RAISE EXCEPTION 'received model response is immutable'; END IF;
          END LOOP;
        END IF;
        RETURN NEW;
      END $$""")
    op.execute("CREATE TRIGGER model_identity BEFORE UPDATE ON model_calls FOR EACH ROW EXECUTE FUNCTION forge_model_identity()")
    op.execute("""CREATE FUNCTION forge_control_relationship() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE action_key text;
      BEGIN
        IF TG_OP='UPDATE' AND (NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR NEW.id IS DISTINCT FROM OLD.id)
          THEN RAISE EXCEPTION 'control identity is immutable'; END IF;
        action_key := CASE WHEN TG_TABLE_NAME='inbox' THEN NEW.data->'payload'->>'action_id' ELSE NEW.data->>'action_id' END;
        IF TG_OP='UPDATE' AND action_key IS DISTINCT FROM
          (CASE WHEN TG_TABLE_NAME='inbox' THEN OLD.data->'payload'->>'action_id' ELSE OLD.data->>'action_id' END)
          THEN RAISE EXCEPTION 'control action binding is immutable'; END IF;
        IF action_key IS NOT NULL AND NOT EXISTS (SELECT 1 FROM actions a WHERE a.tenant_id=NEW.tenant_id AND a.id=action_key)
          THEN RAISE EXCEPTION 'control action must belong to the same tenant'; END IF;
        RETURN NEW;
      END $$""")
    for table in ["inbox", "outbox"]:
        op.execute(f"CREATE TRIGGER control_relationship BEFORE INSERT OR UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION forge_control_relationship()")
        op.execute(f"CREATE INDEX {table}_action_status ON {table} (tenant_id,status,((data->>'action_id')))")
    op.execute("CREATE INDEX inbox_callback_action ON inbox (tenant_id,status,((data->'payload'->>'action_id')))")
    op.execute("CREATE INDEX model_call_receipt_state ON model_calls (tenant_id,run_id,status,((data->>'receipt_state')))")
    op.execute("CREATE INDEX budget_entry_run_status ON budget_entries (tenant_id,status,((data->>'run_id')))")
    op.execute("CREATE UNIQUE INDEX attempt_operation_number ON action_attempts (tenant_id,((data->>'action_id')),((data->>'number')))")
    op.execute("CREATE UNIQUE INDEX turn_model_call ON turns (tenant_id,((data->>'call_id')))")
    op.execute("ALTER TABLE budget_accounts ADD CONSTRAINT budget_nonnegative CHECK (spent>=0 AND reserved>=0 AND limit_micros>=0) NOT VALID")
    op.execute("ALTER TABLE budget_entries ADD CONSTRAINT entry_nonnegative CHECK (reserved>=0 AND (actual IS NULL OR actual>=0)) NOT VALID")
    op.execute("""CREATE FUNCTION forge_budget_identity() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF NOT EXISTS (SELECT 1 FROM runs r WHERE r.tenant_id=NEW.tenant_id AND r.id=NEW.data->>'run_id' AND r.root_id=NEW.account_id)
          THEN RAISE EXCEPTION 'budget run must belong to the account root and tenant'; END IF;
        IF TG_OP='INSERT' THEN RETURN NEW; END IF;
        IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR NEW.id IS DISTINCT FROM OLD.id
          OR NEW.account_id IS DISTINCT FROM OLD.account_id OR NEW.operation_id IS DISTINCT FROM OLD.operation_id
          OR NEW.reserved IS DISTINCT FROM OLD.reserved OR (NEW.data->'run_id')::jsonb IS DISTINCT FROM (OLD.data->'run_id')::jsonb
          THEN RAISE EXCEPTION 'budget operation identity is immutable'; END IF;
        IF OLD.status='settled' AND (NEW.status<>'settled' OR NEW.actual IS DISTINCT FROM OLD.actual)
          THEN RAISE EXCEPTION 'settled budget is immutable'; END IF;
        RETURN NEW;
      END $$""")
    op.execute("CREATE TRIGGER budget_identity BEFORE INSERT OR UPDATE ON budget_entries FOR EACH ROW EXECUTE FUNCTION forge_budget_identity()")
    op.execute("""CREATE FUNCTION forge_finance_delete() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN RAISE EXCEPTION 'operation and financial ledgers cannot be deleted'; END $$""")
    for table in ["actions", "model_calls", "budget_entries", "budget_accounts"]:
        op.execute(f"CREATE TRIGGER finance_delete BEFORE DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION forge_finance_delete()")


def downgrade():
    raise RuntimeError("Evidence and financial integrity require a reviewed forward migration")
