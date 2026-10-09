import json
import os
import shutil

import pytest
from forgeagent.backup import file_digest, pg_tool, restore, validate
from forgeagent.domain import Fault, canonical, digest, uid
from forgeagent.storage import ObjectStore


def test_backup_validates_all_objects_and_detects_corruption(tmp_path):
    store = ObjectStore()
    store.root, store.s3 = tmp_path / "objects", None
    ref = store.put("backup-tenant", "run", b"durable evidence")
    (tmp_path / "database.dump").write_bytes(b"database")
    manifest = {"version": 1, "database_digest": file_digest(tmp_path / "database.dump"),
                "objects": [{"tenant": "backup-tenant", "ref": ref}]}
    manifest["digest"] = digest(manifest)
    (tmp_path / "manifest.json").write_bytes(canonical(manifest))
    assert validate(tmp_path)["objects"] == manifest["objects"]
    store.local_path(ref["key"]).write_bytes(b"forged")
    with pytest.raises(Fault, match="checksum"):
        validate(tmp_path)


def test_restore_cannot_target_production_database_or_existing_directory(tmp_path):
    with pytest.raises(Fault, match="isolated"):
        restore("postgresql+psycopg://admin:secret@localhost/forge", tmp_path, "forge", tmp_path)


def test_pg_credentials_use_environment_and_failure_is_redacted(monkeypatch):
    import subprocess

    def failed(argv, **kwargs):
        assert "secret" not in str(argv)
        assert kwargs["env"]["PGPASSWORD"] == "secret"
        return subprocess.CompletedProcess(argv, 1, b"", b"provider error secret")
    monkeypatch.setattr(subprocess, "run", failed)
    with pytest.raises(Fault) as error:
        pg_tool("pg_dump", "postgresql+psycopg://admin:secret@localhost/forge", [])
    assert "secret" not in str(error.value)


def test_manifest_rewrite_is_detected(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"version": 1, "objects": [], "digest": "forged"}))
    with pytest.raises(Fault, match="manifest"):
        validate(tmp_path)


@pytest.mark.skipif(os.environ.get("FORGE_TEST_BACKUP") != "1", reason="Requires private administrator URL and PostgreSQL client tools")
def test_real_postgres_and_object_snapshot_restore_in_isolated_databases(tmp_path, monkeypatch):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from forgeagent import backup, db
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    from test_migrations import revision

    url = os.environ["FORGE_BACKUP_DATABASE_URL"]
    dump = os.environ.get("FORGE_PG_DUMP", "pg_dump")
    restore_tool = os.environ.get("FORGE_PG_RESTORE", "pg_restore")
    assert shutil.which(dump) and shutil.which(restore_tool), "Release drill requires matching PostgreSQL client tools"
    suffix = uid().replace("-", "")
    source_db, target_db = "forge_restore_src_" + suffix, "forge_restore_dst_" + suffix
    admin = create_engine(url, hide_parameters=True)
    source_engine = None
    try:
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(text('CREATE DATABASE "' + source_db + '" OWNER forge'))
        source_url = make_url(url).set(database=source_db)
        source_engine = create_engine(source_url, hide_parameters=True)
        with source_engine.begin() as connection:
            connection.execute(text("SET ROLE forge"))
            with Operations.context(MigrationContext.configure(connection)):
                for name in ["0001_runtime.py", "0002_constraints.py", "0003_evaluation_datasets.py", "0004_resource_budget.py",
                             "0005_knowledge_erasure.py", "0006_settled_erasure_guards.py", "0007_record_integrity.py", "0008_query_pages.py"]:
                    revision(name).upgrade()
            connection.execute(text("CREATE TABLE alembic_version(version_num varchar(32) PRIMARY KEY)"))
            connection.execute(text("INSERT INTO alembic_version VALUES('0008')"))
            connection.execute(text("RESET ROLE"))
        store = ObjectStore()
        store.s3, store.root = None, tmp_path / "source-objects"
        monkeypatch.setattr(backup, "objects", store)
        ref = store.put("restore-tenant", "project", b"joint-snapshot-evidence")
        with source_engine.begin() as connection:
            connection.execute(db.Tenant.__table__.insert().values(id="restore-tenant"))
            connection.execute(db.Project.__table__.insert().values(tenant_id="restore-tenant", id="project",
                                                                     status="active", data={"evidence_ref": ref}))
        result = backup.export(source_url, tmp_path / "backup", dump)
        assert result["objects"] == 1
        report = restore(url, tmp_path / "backup", target_db, tmp_path / "restored", restore_tool)
        assert report["objects_verified"] == 1 and not report["workers_started"]
        restored_engine = create_engine(make_url(url).set(database=target_db), hide_parameters=True)
        try:
            with restored_engine.connect() as connection:
                assert connection.scalar(text("SELECT bool_and(tableowner='forge') FROM pg_tables WHERE schemaname='public'"))
        finally:
            restored_engine.dispose()
        verified = ObjectStore()
        verified.s3, verified.root = None, tmp_path / "restored" / "objects"
        assert verified.get("restore-tenant", ref) == b"joint-snapshot-evidence"
    finally:
        if source_engine:
            source_engine.dispose()
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            # Names generated by this test only; never drop the source administrator DB.
            for name in [source_db, target_db]:
                if connection.scalar(text("SELECT 1 FROM pg_database WHERE datname=:name"), {"name": name}):
                    connection.execute(text('DROP DATABASE "' + name + '" WITH (FORCE)'))
        admin.dispose()
