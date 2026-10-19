"""Opt-in paid historical pilot. Execute external code only in the pinned sandbox."""
import argparse
import asyncio
import hashlib
import json
import subprocess
from pathlib import Path

from forgeagent import db, evaluations, service
from forgeagent.api import ProjectInput, prepare_project
from forgeagent.auth import Identity
from forgeagent.benchmarks import normalize_external, prepare
from forgeagent.config import settings
from forgeagent.domain import TERMINAL, uid
from forgeagent.repository import commit_files
from forgeagent.sandbox import sandbox
from forgeagent.storage import objects
from forgeagent.worker import Worker
from forgeagent.workspace import write_tree

CASES = Path("benchmarks/external-v1")


def save(path, value):
    data = json.dumps(value, indent=2, ensure_ascii=False).encode()
    # Credentials never belong in the public evidence, even in generated text.
    if settings.model_api_key and settings.model_api_key.encode() in data:
        raise RuntimeError("Credential detected; refusing evidence export")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def export(destination):
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf8"))
    tenant = manifest["tenant"]
    with db.transaction(tenant) as session:
        record = db.get(session, db.Evaluation, tenant, manifest["experiment_id"])
        report = evaluations.report(session, record, tenant)
        save(destination / "report.json", report)
        for entry in record.data["entries"]:
            run = db.get(session, db.Run, tenant, entry["id"])
            folder = destination / entry["case_id"] / entry["config"]
            folder.mkdir(parents=True, exist_ok=True)
            patches = []
            for artifact in db.rows(session, db.Artifact, tenant, run_id=run.id):
                if artifact.kind != "patch":
                    continue
                data = objects.get(tenant, artifact.ref)
                if settings.model_api_key and settings.model_api_key.encode() in data:
                    raise RuntimeError("Credential detected in patch")
                name = f"attempt-{artifact.version}.patch"
                (folder / name).write_bytes(data)
                patches.append({"file": name, "verified": artifact.verified,
                                "sha256": hashlib.sha256(data).hexdigest()})
            calls = sorted(db.rows(session, db.ModelCall, tenant, run_id=run.id),
                           key=lambda call: call.data.get("ordinal", 0))
            actions = db.rows(session, db.Action, tenant, run_id=run.id)
            verifications = db.rows(session, db.VerificationResult, tenant, run_id=run.id)
            save(folder / "evidence.json", {
                "run_id": run.id, "status": run.status, "reason": run.state.get("reason"),
                "patches": patches,
                "calls": [{"id": c.id, "status": c.status, **{key: c.data.get(key) for key in
                    ["ordinal", "model_id", "usage", "receipt_state", "error_category",
                     "failure_phase", "dispatch_disposition", "status_code", "provider_request_id"]},
                    "prices": {key: value for key, value in c.data.get("rate_card", {}).items()
                               if "price" in key}} for c in calls],
                "actions": [{"tool": a.tool, "status": a.status, "attempt": a.attempt} for a in actions],
                "verification": [{key: v.data.get(key) for key in
                    ["verdict", "failure_class", "checks", "result", "build_result", "image_digest",
                     "baseline_digest", "workspace_digest", "artifact_digest"]} for v in verifications],
                "manual_interventions_during_run": 0,
                "intervention_scope": "Serial automatic Worker; no source edits, resume, or runtime hints. Task selection, acceptance authoring and environment preparation were human/assistant setup; their time and cost are unmeasured.",
            })


async def run(args):
    if args.output.exists():
        raise SystemExit("Use a new evidence directory; use --export to refresh existing evidence")
    if not args.validate_only and subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"]):
        raise SystemExit("Commit tracked changes before paid execution")
    if not (args.execute_paid or args.validate_only) or not args.image or not args.repositories:
        raise SystemExit("Requires --execute-paid, --image sha256:..., --repositories path")
    if not args.image.startswith("sha256:") or args.total_cap_usd != "4.00":
        raise SystemExit("This reviewed pilot reserves four $1 runs; set --total-cap-usd 4.00")
    args.output.mkdir(parents=True)
    settings.sandbox_image = args.image
    tenant = "external-pilot-" + uid()
    actor = "pilot-runner"
    service.provision(tenant, actor)
    cases, validation = [], []
    definitions = json.loads((CASES / "cases.json").read_text(encoding="utf8"))
    for definition in definitions:
        repo_name = definition["repo"].split("/")[-1]
        acceptance = (CASES / definition["acceptance_file"]).read_text(encoding="utf8")
        # Reference solutions are evaluated separately and never enter the model repository.
        for revision in ["base", "reference"]:
            directory = args.repositories / (repo_name + "-" + revision)
            expected = definition["base_commit" if revision == "base" else "reference_commit"]
            actual = subprocess.check_output(["git", "-C", str(directory), "rev-parse", "HEAD"], text=True).strip()
            if expected != actual:
                raise RuntimeError("Source revision mismatch")
            stage = args.output / "validation-workspaces" / (repo_name + "-" + revision)
            write_tree(stage, commit_files(directory, actual))
            write_tree(stage, {"hidden_tests/acceptance.py": acceptance})
            result = await sandbox.execute(stage, ["python", "-m", "hidden_tests.acceptance"], args.image,
                                           timeout=90, readonly=True)
            validation.append({"case": definition["instance_id"], "revision": revision,
                               "commit": actual, "result": result})
            save(args.output / "acceptance-validation.json", validation)
            if result["timed_out"] or result["truncated"] or (result["exit_code"] == 0) != (revision == "reference"):
                raise RuntimeError("Acceptance must fail on baseline and pass on reference")
        if args.validate_only:
            continue
        patch = "--- /dev/null\n+++ b/hidden_tests/acceptance.py\n@@ -0,0 +1," + str(len(acceptance.splitlines())) + " @@\n"
        patch += "".join("+" + line + "\n" for line in acceptance.splitlines())
        reviewed = normalize_external({**definition, "test_patch": patch}, "development")
        # Use the full clone for bundle creation. Linked worktrees are useful for
        # baseline/reference acceptance, but their alternates can race a bare
        # clone while Git is packing objects.
        source_repository = args.repositories / (repo_name + "-full")
        if not source_repository.exists():
            source_repository = args.repositories / repo_name
        prepared = prepare(source_repository, reviewed, args.image,
                           ["python", "-m", "hidden_tests.acceptance"], definition["allowed_paths"], ["hidden_tests"])
        project = ProjectInput.model_validate(prepared["project"])
        data = prepare_project(project, Identity(tenant, actor, True))
        data["acceptance"] = service.seal_acceptance(tenant, "project-" + project.id, data["acceptance"])
        with db.transaction(tenant) as session:
            session.add(db.Project(tenant_id=tenant, id=project.id, data=data))
            authorization = db.get(session, db.Authorization, tenant, actor)
            from forgeagent.auth import PROJECT_OPERATIONS

            authorization.data = {**authorization.data, "project_permissions": {
                **authorization.data["project_permissions"], project.id: sorted(PROJECT_OPERATIONS)}}
        cases.append({**prepared["case"], "budget": {"max_cost_usd": "1.00", "max_turns": 32,
                     "max_wall_seconds": 900, "max_tool_calls": 100}})
    if args.validate_only:
        return
    dataset = evaluations.DatasetInput(id="external-historical@1", cases=cases,
        source="Two public historical defects. Development only; assistant saw upstream patches while authoring acceptance. No gold patch in model workspace. No blind or generalization claim.")
    spec = evaluations.ExperimentInput(dataset_id=dataset.id, repetitions=1, seed=42,
        max_total_cost_usd=args.total_cap_usd,
        configurations=[{"name": "recovery-off", "harness": {"evidence_recovery": False}},
                        {"name": "recovery-on", "harness": {"evidence_recovery": True}}])
    with db.transaction(tenant) as session:
        evaluations.register(session, tenant, dataset)
        experiment = evaluations.start(session, tenant, actor, spec)
    save(args.output / "manifest.json", {"source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "source_state": "clean-tracked-tree", "tenant": tenant, "experiment_id": experiment["id"],
        "image": args.image, "model": settings.model_id, "max_total_cost_usd": "4.00",
        "variable": "evidence_recovery", "dataset": dataset.model_dump(mode="json"),
        "experiment": spec.model_dump(mode="json"), "model_requests_exported": False})
    try:
        for entry in experiment["entries"]:
            worker = Worker(target_run=entry["id"])
            print(f"Starting {entry['case_id']} / {entry['config']} ({entry['id']})", flush=True)
            async with asyncio.timeout(960):
                for step in range(160):
                    await worker.once(tenant)
                    with db.transaction(tenant) as session:
                        current = db.get(session, db.Run, tenant, entry["id"])
                        if step % 8 == 0 or current.status in TERMINAL | {"PAUSED"}:
                            print(f"  {current.status} {current.phase} turn={current.state['turn']}", flush=True)
                        if current.status in TERMINAL | {"PAUSED"}:
                            break
                    await asyncio.sleep(.2)
                else:
                    raise RuntimeError("Worker step cap reached; preserve pending evidence")
            export(args.output)
    finally:
        export(args.output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repositories", type=Path)
    parser.add_argument("--image")
    parser.add_argument("--execute-paid", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--total-cap-usd", default="4.00")
    parser.add_argument("--export", action="store_true")
    args = parser.parse_args()
    if args.export:
        export(args.output)
    else:
        asyncio.run(run(args))


if __name__ == "__main__":
    main()
