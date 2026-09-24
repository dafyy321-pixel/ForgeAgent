"""Enforce immutable action identity and monotonic terminal states."""

from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade():
    op.execute("""ALTER TABLE actions ADD CONSTRAINT action_states CHECK
      (status IN ('PREPARED','WAITING_APPROVAL','READY','DISPATCHED','RUNNING','SUCCEEDED','FAILED','UNKNOWN','CANCEL_REQUESTED','CANCELLED'))""")
    op.execute("""CREATE FUNCTION forge_immutable_action() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF NEW.args::text <> OLD.args::text OR NEW.effect_digest <> OLD.effect_digest
          OR NEW.tool <> OLD.tool OR NEW.run_id <> OLD.run_id OR NEW.effect_class <> OLD.effect_class
          THEN RAISE EXCEPTION 'action intent is immutable'; END IF;
        RETURN NEW;
      END $$""")
    op.execute(
        "CREATE TRIGGER action_identity BEFORE UPDATE ON actions FOR EACH ROW EXECUTE FUNCTION forge_immutable_action()"
    )
    op.execute("""CREATE FUNCTION forge_terminal_run() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF OLD.status IN ('SUCCEEDED','FAILED','CANCELLED') AND NEW.status <> OLD.status
          THEN RAISE EXCEPTION 'terminal run is immutable'; END IF;
        RETURN NEW;
      END $$""")
    op.execute("CREATE TRIGGER terminal_run BEFORE UPDATE ON runs FOR EACH ROW EXECUTE FUNCTION forge_terminal_run()")
    op.execute("CREATE UNIQUE INDEX turn_ordinal ON turns (tenant_id,run_id,(data->>'ordinal'))")
    op.execute(
        "CREATE UNIQUE INDEX attempt_number ON action_attempts (tenant_id,(data->>'action_id'),(data->>'number'))"
    )
    op.execute("CREATE INDEX memory_project_status ON memories (tenant_id,status,(data->>'project'))")


def downgrade():
    raise RuntimeError("Use an explicitly reviewed forward migration")
