import asyncio
import json
import os
from pathlib import Path
from typing import Any

import typer

from .sdk import Client, ForgeError

app = typer.Typer(help="ForgeAgent durable runtime client")
action = typer.Typer()
evaluation = typer.Typer()
project = typer.Typer()
admin = typer.Typer()
app.add_typer(action, name="action")
app.add_typer(evaluation, name="eval")
app.add_typer(project, name="project")
app.add_typer(admin, name="admin")


@admin.command("executor")
def archived_executor(tenant: str, run_id: str, quanta: int = typer.Option(1, min=1, max=1000)):
    """Execute only this run with its internally captured source and exact installed dependencies."""
    from .semantic import execute_archived

    execute_archived(tenant, run_id, quanta)


def client():
    return Client(os.getenv("FORGE_API_URL", "http://127.0.0.1:8000"), os.getenv("FORGE_ACCESS_TOKEN"))


def execute(fn):
    async def call():
        async with client() as c:
            try:
                result = await fn(c)
                typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
            except ForgeError as exc:
                typer.echo(json.dumps(exc.payload, ensure_ascii=False), err=True)
                raise typer.Exit(1)

    asyncio.run(call())


@app.command()
def run(spec: Path, key: str | None = None):
    """Submit a versioned task JSON file."""
    execute(lambda c: c.create(json.loads(spec.read_text(encoding="utf-8")), key))


@app.command()
def status(id: str):
    execute(lambda c: c.status(id))


@app.command()
def pause(id: str):
    execute(lambda c: c.control(id, "pause"))


@app.command()
def resume(id: str):
    execute(lambda c: c.control(id, "resume"))


@app.command()
def cancel(id: str):
    execute(lambda c: c.control(id, "cancel"))


@app.command()
def events(id: str, after: int = 0):
    async def stream():
        async with client() as c:
            async for e in c.events(id, after):
                typer.echo(json.dumps(e, ensure_ascii=False))

    asyncio.run(stream())


@app.command()
def replay(id: str):
    """Read-only audit replay: does not call models or tools."""
    execute(lambda c: c.request("GET", f"/runs/{id}/replay"))


@app.command()
def fork(id: str):
    async def call(c):
        import uuid

        r = await c.status(id)
        return await c.request(
            "POST", f"/runs/{id}/fork", {"expected_version": r["stateVersion"], "reason": "CLI fork"}, str(uuid.uuid4())
        )

    execute(call)


@app.command()
def artifacts(id: str):
    execute(lambda c: c.request("GET", f"/runs/{id}/artifacts"))


@action.command("inspect")
def inspect_action(id: str):
    execute(lambda c: c.request("GET", f"/actions/{id}"))


@action.command("reconcile")
def reconcile(id: str, outcome: str, evidence: str):
    async def call(c):
        a = await c.request("GET", f"/actions/{id}")
        r = await c.status(a["run_id"])
        return await c.request(
            "POST",
            f"/actions/{id}/reconcile",
            {
                "expected_version": r["stateVersion"],
                "reason": "CLI reconciliation",
                "outcome": outcome,
                "evidence": evidence,
            },
        )

    execute(call)


@evaluation.command("run")
def eval_run(repetitions: int = 3, seed: int = 42):
    execute(lambda c: c.request("POST", "/evaluations", {"repetitions": repetitions, "seed": seed}))


@evaluation.command("report")
def eval_report(id: str):
    execute(lambda c: c.request("GET", f"/evaluations/{id}"))


@project.command("register")
def register_project(id: str, directory: Path, acceptance: Path, commit: str = "HEAD", dry_run: bool = False):
    """Upload a bounded Git bundle at a fixed commit and a protected acceptance contract."""
    import base64

    from .repository import bundle_from_directory, commit_files

    contract = json.loads(acceptance.read_text(encoding="utf-8"))
    from .domain import AcceptanceContract, BuildContract

    build = BuildContract(**contract.pop("build", {})).model_dump()
    contract = AcceptanceContract(**contract).model_dump()
    from .domain import Fault, canonical, digest

    try:
        repository = bundle_from_directory(directory, commit)
        manifest = commit_files(directory.resolve(), repository.commit)
        bundle = base64.b64decode(repository.bundle_base64, validate=True)
        if len(bundle) > 16 * 1024 * 1024:
            raise Fault("REPOSITORY_LIMIT", "Git bundle exceeds 16 MiB; export a smaller reviewed repository history", 413)
        payload: dict[str, Any] = {"id": id, "name": id, "repository": {"commit": repository.commit,
            "bundle_digest": digest(bundle), "bundle_bytes": len(bundle)},
            "acceptance_id": contract["id"], "verification_argv": contract["argv"], "acceptance": contract, "build": build}
        if len(canonical(payload)) > 2 * 1024 * 1024:
            raise Fault("BODY_LIMIT", "Acceptance and project metadata exceed the 2 MiB JSON request limit", 413)
    except Fault as exc:
        raise typer.BadParameter(exc.message) from exc
    if dry_run:
        typer.echo(json.dumps({"commit": repository.commit, "bundle_bytes": len(bundle), "json_bytes": len(canonical(payload)),
            "files": len(manifest), "baseline_digest": digest(manifest), "acceptance_execution": "not_run"}))
        return
    async def upload_and_register(c):
        payload["repository"].update(await c.upload_repository(bundle))
        await c.request("POST", "/projects/preflight", payload)
        return await c.request("POST", "/projects", payload)
    execute(upload_and_register)


@admin.command("provision")
def provision_identity(tenant: str, subject: str):
    """Local administrator operation; requires database credentials, not a browser token."""
    from .service import provision

    provision(tenant, subject)
    typer.echo(json.dumps({"tenant": tenant, "subject": subject}))


@admin.command("health")
def health():
    """Inspect pending operational work and its recovery instructions."""
    execute(lambda c: c.request("GET", "/operations/health"))


@admin.command("gc")
def collect_objects(apply: bool = False, min_age_hours: int = 24):
    """Preview eligible local orphan objects; --apply reclaims only unreferenced settled-run objects."""
    execute(lambda c: c.request("POST", "/operations/objects/gc", {"apply": apply, "min_age_hours": min_age_hours}))


@admin.command("backup")
def backup(destination: Path, pg_dump: str = "pg_dump"):
    """Offline joint snapshot. Administrator URL is read only from FORGE_BACKUP_DATABASE_URL."""
    from .backup import export

    url = os.environ.get("FORGE_BACKUP_DATABASE_URL")
    if not url:
        raise typer.BadParameter("Configure FORGE_BACKUP_DATABASE_URL in private secret storage")
    try:
        typer.echo(json.dumps(export(url, destination, pg_dump)))
    except Exception as exc:
        from .domain import Fault

        typer.echo(exc.message if isinstance(exc, Fault) else "Backup failed; inspect private operator logs", err=True)
        raise typer.Exit(1) from None


@admin.command("restore-drill")
def restore_drill(source: Path, database: str, destination: Path, pg_restore: str = "pg_restore"):
    """Verify backup by restoring into a new forge_restore_* database and new object directory."""
    from .backup import restore

    url = os.environ.get("FORGE_BACKUP_DATABASE_URL")
    if not url:
        raise typer.BadParameter("Configure FORGE_BACKUP_DATABASE_URL in private secret storage")
    try:
        typer.echo(json.dumps(restore(url, source, database, destination, pg_restore)))
    except Exception as exc:
        from .domain import Fault

        typer.echo(exc.message if isinstance(exc, Fault) else "Restore drill failed; inspect private operator logs", err=True)
        raise typer.Exit(1) from None


@evaluation.command("dataset")
def register_dataset(spec: Path):
    execute(lambda c: c.request("POST", "/evaluation-datasets", json.loads(spec.read_text(encoding="utf-8"))))


@evaluation.command("experiment")
def run_experiment(spec: Path):
    execute(lambda c: c.request("POST", "/experiments", json.loads(spec.read_text(encoding="utf-8"))))
