"""Export one PostgreSQL snapshot together with every referenced immutable object.

Run during a stopped-writer maintenance window. Restore only into a newly created
forge_restore_* database and a new directory; production databases are never targets.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from sqlalchemy import JSON, create_engine, select, text
from sqlalchemy.engine import make_url

from . import db
from .domain import Fault, canonical, digest, now
from .storage import ObjectStore, objects
from .telemetry import observed


def references(value):
    if isinstance(value, dict):
        if {"key", "digest", "bytes"} <= value.keys():
            yield {k: value[k] for k in ("key", "digest", "bytes")}
        for nested in value.values():
            yield from references(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from references(nested)


def all_references(connection):
    result = {}
    for table in db.Base.metadata.sorted_tables:
        columns = [column for column in table.c if isinstance(column.type, JSON)]
        if "tenant_id" not in table.c or not columns:
            continue
        for row in connection.execute(select(table.c.tenant_id, *columns)):
            for value in row[1:]:
                for ref in references(value):
                    identity = (row[0], ref["key"])
                    if identity in result and result[identity] != ref:
                        raise Fault("BACKUP_REFERENCE", "Conflicting immutable object references")
                    result[identity] = ref
    return [{"tenant": tenant, "ref": ref} for (tenant, _), ref in sorted(result.items())]


def admin_engine(url):
    engine = create_engine(url, hide_parameters=True)
    with engine.connect() as connection:
        if not connection.scalar(text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")):
            engine.dispose()
            raise Fault("BACKUP_ROLE", "Backup/restore requires an offline administrator role with BYPASSRLS", 403)
    return engine


def pg_tool(name, url, arguments, executable=None):
    connection = make_url(url)
    environment = dict(os.environ)
    environment.update(PGHOST=connection.host or "127.0.0.1", PGPORT=str(connection.port or 5432),
                       PGUSER=connection.username or "", PGDATABASE=connection.database or "",
                       PGCONNECT_TIMEOUT="10")
    if connection.password:
        environment["PGPASSWORD"] = connection.password
    if "sslmode" in connection.query:
        sslmode = connection.query["sslmode"]
        if not isinstance(sslmode, str):
            raise Fault("BACKUP_URL", "Specify exactly one PostgreSQL sslmode", 422)
        environment["PGSSLMODE"] = sslmode
    result = subprocess.run([executable or name, *arguments], env=environment, capture_output=True,
                            timeout=3600, check=False)
    if result.returncode:
        raise Fault("BACKUP_TOOL", f"{name} failed; inspect private server logs", 503)


def file_digest(path):
    with path.open("rb") as source:
        return "sha256:" + hashlib.file_digest(source, "sha256").hexdigest()


@observed("backup.export")
def export(url, destination, pg_dump=None):
    destination = Path(destination).absolute()
    if destination.exists() or any(path.is_symlink() or path.is_junction() for path in destination.parents):
        raise Fault("BACKUP_PATH", "Backup destination must be a new directory with no filesystem links", 422)
    engine = admin_engine(url)
    destination.mkdir(parents=True)
    try:
        with engine.connect() as connection:
            # Session locks survive the boundary into REPEATABLE READ; erasure/GC/publication
            # share these locks. All DB writers are then excluded from the exported snapshot.
            connection.execute(text("SET LOCAL lock_timeout='15s'"))
            connection.execute(text("LOCK TABLE tenants IN SHARE MODE"))
            tenants = list(connection.scalars(select(db.Tenant.id).order_by(db.Tenant.id)))
            for tenant in tenants:
                connection.execute(text("SELECT pg_advisory_lock(hashtextextended(:key,0))"),
                                   {"key": tenant + ":knowledge-lifecycle"})
            connection.commit()
            try:
                connection = connection.execution_options(isolation_level="REPEATABLE READ")
                with connection.begin():
                    tables = ",".join('"' + name + '"' for name in sorted(db.Base.metadata.tables))
                    connection.execute(text("SET LOCAL lock_timeout='15s'"))
                    connection.execute(text("LOCK TABLE " + tables + " IN SHARE MODE"))
                    if list(connection.scalars(select(db.Tenant.id).order_by(db.Tenant.id))) != tenants:
                        raise Fault("BACKUP_TENANTS", "Tenant set changed before snapshot; retry backup")
                    snapshot = connection.scalar(text("SELECT pg_export_snapshot()"))
                    refs = all_references(connection)
                    pg_tool("pg_dump", url, ["--format=custom", "--snapshot=" + snapshot,
                            "--file=" + str(destination / "database.dump")], pg_dump)
                    target = ObjectStore()
                    target.s3, target.root = None, destination / "objects"
                    target.root.mkdir()
                    for item in refs:
                        tenant, ref = item["tenant"], item["ref"]
                        path = target.local_path(ref["key"])
                        path.parent.mkdir(parents=True, exist_ok=True)
                        with objects.open(tenant, ref) as source, path.open("wb") as output:
                            shutil.copyfileobj(source, output, 65536)
                        target.verify(tenant, ref)
                    manifest = {"version": 1, "created_at": now().isoformat(), "objects": refs,
                        "database_digest": file_digest(destination / "database.dump"),
                        "migration": connection.scalar(text("SELECT version_num FROM alembic_version")),
                        "storage_source": "s3" if objects.s3 else "local"}
                    manifest["digest"] = digest(manifest)
                    (destination / "manifest.json").write_bytes(canonical(manifest))
            finally:
                connection.rollback()
                connection.execute(text("SELECT pg_advisory_unlock_all()"))
                connection.commit()
        return {"backup": str(destination), "objects": len(refs), "digest": manifest["digest"]}
    except BaseException:
        # Preserve an incomplete backup for diagnosis, but never publish a success manifest.
        (destination / "manifest.json").unlink(missing_ok=True)
        raise
    finally:
        engine.dispose()


def validate(source):
    source = Path(source).resolve()
    manifest = json.loads((source / "manifest.json").read_bytes())
    if manifest.get("version") != 1 or manifest.get("digest") != digest({k: v for k, v in manifest.items() if k != "digest"}):
        raise Fault("BACKUP_CORRUPT", "Backup manifest checksum mismatch")
    if file_digest(source / "database.dump") != manifest["database_digest"]:
        raise Fault("BACKUP_CORRUPT", "Database backup checksum mismatch")
    target = ObjectStore()
    target.s3, target.root = None, source / "objects"
    for item in manifest["objects"]:
        target.verify(item["tenant"], item["ref"])
    return manifest


@observed("backup.restore_drill")
def restore(url, source, database, destination, pg_restore=None):
    if not re.fullmatch(r"forge_restore_[a-z0-9_]{1,40}", database) or database == make_url(url).database:
        raise Fault("RESTORE_TARGET", "Restore requires a new isolated forge_restore_* database", 422)
    manifest = validate(source)
    destination = Path(destination).absolute()
    if destination.exists() or any(path.is_symlink() or path.is_junction() for path in destination.parents):
        raise Fault("RESTORE_TARGET", "Restore destination must be a new directory with no filesystem links", 422)
    engine = admin_engine(url)
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            # Regex-enforced identifier, CREATE without overwrite or DROP.
            connection.execute(text('CREATE DATABASE "' + database + '"'))
        restored_url = make_url(url).set(database=database)
        pg_tool("pg_restore", restored_url, ["--exit-on-error",
                "--dbname=" + database, str(Path(source).resolve() / "database.dump")], pg_restore)
        shutil.copytree(Path(source).resolve() / "objects", destination / "objects")
        restored = admin_engine(restored_url)
        try:
            with restored.connect() as connection:
                if all_references(connection) != manifest["objects"]:
                    raise Fault("RESTORE_REFERENCES", "Restored database references differ from the backup snapshot")
                if connection.scalar(text("SELECT version_num FROM alembic_version")) != manifest["migration"]:
                    raise Fault("RESTORE_SCHEMA", "Restored migration version differs from the backup")
                policies = connection.scalar(text("SELECT count(*) FROM pg_policies WHERE schemaname='public' AND policyname='tenant_isolation'"))
                if policies != len(db.Base.metadata.tables) - 1:
                    raise Fault("RESTORE_RLS", "Restored tenant isolation policies are incomplete")
            target = ObjectStore()
            target.s3, target.root = None, destination / "objects"
            for item in manifest["objects"]:
                target.verify(item["tenant"], item["ref"])
        finally:
            restored.dispose()
        report = {"database": database, "data_dir": str(destination), "objects_verified": len(manifest["objects"]),
                  "backup_digest": manifest["digest"], "verified_at": now().isoformat(), "workers_started": False}
        (destination / "restore-report.json").write_bytes(canonical(report))
        return report
    finally:
        engine.dispose()
