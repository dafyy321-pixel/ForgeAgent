import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from forgeagent import db
from forgeagent.domain import uid
from sqlalchemy import inspect


def revision(name):
    path = Path(__file__).resolve().parents[2] / "migrations" / "versions" / name
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frozen_migrations_build_current_schema_without_live_metadata():
    schema = "migration_test_" + uid().replace("-", "")
    with db.engine.begin() as connection:
        # The transaction rolls back the complete test-owned schema, even if any assertion fails.
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
        try:
            with Operations.context(MigrationContext.configure(connection)):
                revision("0001_runtime.py").upgrade()
                inspector = inspect(connection)
                assert "evaluation_datasets" not in inspector.get_table_names(schema=schema)
                assert "resources" not in {c["name"] for c in inspector.get_columns("budget_accounts", schema=schema)}
                revision("0002_constraints.py").upgrade()
                revision("0003_evaluation_datasets.py").upgrade()
                revision("0004_resource_budget.py").upgrade()
                revision("0005_knowledge_erasure.py").upgrade()
                revision("0006_settled_erasure_guards.py").upgrade()
                revision("0007_record_integrity.py").upgrade()
                revision("0008_query_pages.py").upgrade()
                revision("0009_workspace_revisions.py").upgrade()
            inspector = inspect(connection)
            assert set(inspector.get_table_names(schema=schema)) == set(db.Base.metadata.tables)
            for name, table in db.Base.metadata.tables.items():
                columns = inspector.get_columns(name, schema=schema)
                assert {(c["name"], c["nullable"]) for c in columns} == {(c.name, c.nullable) for c in table.columns}
                for column in columns:
                    assert column["type"].compile(dialect=connection.dialect) == table.c[column["name"]].type.compile(
                        dialect=connection.dialect
                    )
            count = connection.exec_driver_sql(
                "SELECT count(*) FROM pg_policies WHERE schemaname = %s AND policyname = 'tenant_isolation'", (schema,)
            ).scalar()
            assert count == len(db.Base.metadata.tables) - 1
        finally:
            connection.rollback()
