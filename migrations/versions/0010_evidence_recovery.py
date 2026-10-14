"""Tenant-scoped immutable evidence, diagnoses and recovery intents."""

from alembic import op

revision = "0010"
down_revision = "0009"
TABLES = ("evidence_records", "diagnoses", "recovery_attempts")


def upgrade():
    for table in TABLES:
        op.execute(f"""CREATE TABLE {table} (
            tenant_id varchar(100) NOT NULL, id varchar(100) NOT NULL,
            created_at timestamptz NOT NULL, data json NOT NULL, status varchar(40) NOT NULL,
            run_id varchar(100) NOT NULL, PRIMARY KEY(tenant_id,id),
            FOREIGN KEY(tenant_id,run_id) REFERENCES runs(tenant_id,id))""")
        op.execute(f"CREATE INDEX ix_{table}_run_id ON {table}(run_id)")
        op.execute(f"CREATE INDEX {table}_page ON {table}(tenant_id,run_id,created_at DESC,id DESC)")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""CREATE POLICY tenant_isolation ON {table}
            USING(tenant_id=current_setting('forge.tenant_id',true))
            WITH CHECK(tenant_id=current_setting('forge.tenant_id',true))""")
    op.execute("""CREATE FUNCTION forge_recovery_integrity() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE linked text;
      BEGIN
        IF TG_OP='DELETE' THEN
          IF forge_erasure_authorized(OLD.tenant_id,OLD.run_id) AND EXISTS
            (SELECT 1 FROM runs WHERE tenant_id=OLD.tenant_id AND id=OLD.run_id
             AND state->>'knowledge_erased'='true') THEN RETURN OLD; END IF;
          RAISE EXCEPTION 'recovery evidence requires registered erasure';
        END IF;
        IF TG_OP='UPDATE' THEN
          IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR NEW.id IS DISTINCT FROM OLD.id
            OR NEW.run_id IS DISTINCT FROM OLD.run_id OR NEW.created_at IS DISTINCT FROM OLD.created_at
            OR NEW.data::jsonb IS DISTINCT FROM OLD.data::jsonb
            THEN RAISE EXCEPTION 'recovery record identity and data are immutable'; END IF;
        END IF;
        IF TG_TABLE_NAME='diagnoses' THEN
          FOR linked IN SELECT json_array_elements_text(NEW.data->'evidence_ids') LOOP
            IF NOT EXISTS(SELECT 1 FROM evidence_records e WHERE e.tenant_id=NEW.tenant_id
              AND e.run_id=NEW.run_id AND e.id=linked) THEN RAISE EXCEPTION 'diagnosis evidence scope'; END IF;
          END LOOP;
        END IF;
        IF TG_TABLE_NAME='recovery_attempts' AND NOT EXISTS
          (SELECT 1 FROM diagnoses d WHERE d.tenant_id=NEW.tenant_id AND d.run_id=NEW.run_id
           AND d.id=NEW.data->>'diagnosis_id') THEN RAISE EXCEPTION 'recovery diagnosis scope'; END IF;
        RETURN NEW;
      END $$""")
    for table in TABLES:
        op.execute(f"CREATE TRIGGER recovery_integrity BEFORE INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION forge_recovery_integrity()")


def downgrade():
    raise RuntimeError("Recovery evidence requires a reviewed forward migration")
