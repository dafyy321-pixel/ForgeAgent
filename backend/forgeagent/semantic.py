"""Execution bindings and immutable, locally captured executor archives."""

import importlib.metadata
import json
import platform
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

from .domain import Fault, digest
from .storage import objects

STAGES = {
    "state": ["domain.py", "db.py", "reducer.py", "auth.py"],
    "runtime": ["service.py", "worker.py", "progress.py", "concurrency.py", "semantic.py"],
    "decision": ["models.py", "model_protocol.py", "model_retry.py", "context.py", "context_summary.py", "context_cost.py", "billing.py", "tokenization.py"],
    "effects": ["sandbox.py", "sandbox_manager.py", "resources.py", "observation.py", "workspace.py", "repository.py", "repo_tools.py", "remote.py", "storage.py"],
    "verification": ["verification.py"],
}
DEPENDENCIES = ["sqlalchemy", "psycopg", "pydantic", "pydantic-settings", "httpx", "boto3",
                "openai", "anthropic", "mcp", "a2a-sdk", "opentelemetry-api", "fastapi", "starlette",
                "PyJWT", "cryptography", "httpcore", "anyio", "pydantic-core", "botocore",
                "opentelemetry-sdk", "opentelemetry-exporter-otlp-proto-http", "prometheus-client", "tiktoken", "regex"]


def environment():
    return {"python": platform.python_version(), "platform": sys.platform,
            "dependencies": {name: importlib.metadata.version(name) for name in DEPENDENCIES}}


@lru_cache(maxsize=1)
def archive():
    root = Path(__file__).parent
    return {"schema_version": 1, "environment": environment(),
            "sources": {p.name: p.read_text(encoding="utf-8") for p in sorted(root.glob("*.py"))}}


@lru_cache(maxsize=1)
def bindings():
    saved = archive()
    return {"schema_version": 2, "environment": saved["environment"],
            "stages": {stage: {name: digest(saved["sources"][name].encode()) for name in names}
                       for stage, names in STAGES.items()}}


def store_archive(tenant):
    return objects.put(tenant, "executors", archive())


def materialize(tenant, run, directory):
    ref = run.state.get("executor_ref")
    if not ref:
        raise Fault("EXECUTOR_UNAVAILABLE", "This legacy task has no captured executor; migrate or fork explicitly")
    saved = json.loads(objects.get(tenant, ref))
    implementation = run.state["semantic"]["implementation"]
    if saved.get("schema_version") != 1 or implementation.get("schema_version") != 2:
        raise Fault("EXECUTOR_UNAVAILABLE", "Unsupported executor archive format")
    if saved["environment"] != implementation["environment"] or saved["environment"] != environment():
        raise Fault("EXECUTOR_DEPENDENCIES", "Install the exact archived Python and dependency versions before execution")
    for stage in implementation["stages"].values():
        for name, checksum in stage.items():
            if name not in saved["sources"] or digest(saved["sources"][name].encode()) != checksum:
                raise Fault("EXECUTOR_CORRUPT", "Archived executable module differs from its pinned binding")
    package = Path(directory) / "forgeagent"
    package.mkdir(parents=True, exist_ok=False)
    for name, source in saved["sources"].items():
        if Path(name).name != name or not name.endswith(".py"):
            raise Fault("EXECUTOR_CORRUPT", "Invalid archived module path")
        (package / name).write_text(source, encoding="utf-8", newline="")
    return package.parent


def execute_archived(tenant, run_id, quanta=1):
    from . import db

    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        if run.status not in {"QUEUED", "ACTIVE", "WAITING", "CANCELLING"}:
            raise Fault("INVALID_STATE", "Resume with the archived executor before running its next quantum")
    with tempfile.TemporaryDirectory(prefix="forge-executor-") as directory:
        source = materialize(tenant, run, directory)
        script = ("import asyncio,sys; sys.path.insert(0,sys.argv[1]); "
                  "from forgeagent.worker import Worker; "
                  "asyncio.run(Worker(target_run=sys.argv[3]).archived_quanta(sys.argv[2],int(sys.argv[4])))")
        return subprocess.run([sys.executable, "-I", "-c", script, str(source), tenant, run_id, str(quanta)], check=True)


# Capture before any later file edit can relabel the loaded process.
bindings()
