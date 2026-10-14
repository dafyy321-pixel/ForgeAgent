"""Pinned REA CLI adapter. Inert JavaScript analysis only; REA never grants authority."""

import asyncio
import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import Field

from . import db, diagnosis, processes, resources
from .auth import Identity, project_access
from .concurrency import admission
from .config import settings
from .domain import TOOL_INPUTS, Fault, Strict, digest
from .storage import objects
from .workspace import DENIED, entry, safe_path, validate_manifest, write_tree

VERSION = "6.4.0"
OPERATIONS = {"rea.analyze", "rea.view", "rea.trace"}


class RegisterTarget(Strict):
    project_id: str = Field(min_length=1, max_length=100)
    path: str = Field(min_length=1, max_length=1000)
    label: str = Field(min_length=1, max_length=160)


def file_digest(path):
    with path.open("rb") as source:
        return "sha256:" + hashlib.file_digest(source, "sha256").hexdigest()


def runtime_binding():
    runtime = settings.rea_runtime_dir
    node = shutil.which("node")
    if not runtime or not node:
        raise Fault("REA_UNAVAILABLE", "Configure a locally installed, pinned REA runtime")
    root = runtime.resolve()
    package = root / "node_modules/rea-agents"
    try:
        installed_version = json.loads((package / "package.json").read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError) as exc:
        raise Fault("REA_UNAVAILABLE", "Pinned runtime package is missing or malformed") from exc
    if installed_version != VERSION:
        raise Fault("REA_VERSION", "This adapter requires REA 6.4.0")
    paths = []
    for directory, dirs, names in os.walk(root):
        dirs[:] = sorted(name for name in dirs if name != ".bin")
        for name in sorted([*dirs, *names]):
            path = Path(directory) / name
            if path.is_symlink() or path.is_junction():
                raise Fault("REA_RUNTIME_LINK", "Runtime dependencies must be materialized regular files")
        for name in sorted(names):
            path = Path(directory) / name
            paths.append(path)
    if len(paths) > 20000:
        raise Fault("REA_RUNTIME_LIMIT", "Runtime deployment exceeds the bounded manifest")
    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="rea-integrity") as pool:
        checksums = list(pool.map(lambda path: [path.relative_to(root).as_posix(), file_digest(path)], paths))
    return {"version": VERSION, "runtime_digest": digest(checksums), "node_digest": file_digest(Path(node))}


def readiness():
    package = settings.rea_runtime_dir / "node_modules/rea-agents" if settings.rea_runtime_dir else None
    try:
        version = json.loads((package / "package.json").read_text(encoding="utf-8"))["version"] if package else None
        available = bool(package and (package / "scripts/rea.mjs").is_file() and (package / "dist").is_dir()
                         and version == VERSION and shutil.which("node"))
    except (OSError, ValueError, KeyError):
        version, available = None, False
    return {"static": {"ready": available, "required_version": VERSION, "installed_version": version,
                       "operations": sorted(OPERATIONS), "target_execution": False,
                       "memory_enforcement": "windows_job_1GiB" if os.name == "nt" else "node_heap_only"},
            "dynamic": {"ready": False, "reason": "No OS/provider-specific dynamic observation runner is registered"}}


def capture(path):
    root = Path(path)
    roots = [Path(value).resolve() for value in json.loads(settings.rea_target_roots)]
    if not root.is_absolute() or not any(root.resolve().is_relative_to(base) for base in roots):
        raise Fault("REFERENCE_PATH_DENIED", "Target must be inside an operator-configured reference root", 403)
    if not root.is_dir() or root.is_symlink() or root.is_junction():
        raise Fault("REFERENCE_PATH_DENIED", "Register an extracted regular directory", 403)
    content, total = {}, 0
    excluded = DENIED | {".venv", ".git"}
    for directory, dirs, names in os.walk(root):
        dirs[:] = sorted(name for name in dirs if name not in excluded)
        if len(content) + len(names) > 1000:
            raise Fault("REFERENCE_LIMIT", "Reference exceeds 1000 files")
        for name in sorted([*dirs, *names]):
            path = Path(directory) / name
            if name in excluded:
                continue
            relative = path.relative_to(root).as_posix()
            safe_path(root, relative)
            if not path.is_file():
                if path.is_dir():
                    continue
                raise Fault("REFERENCE_PATH_DENIED", "Only regular reference files are admitted")
            size = path.stat().st_size
            if size > 2 * 1024 * 1024 or total + size > min(settings.max_object_bytes // 2, 8 * 1024 * 1024):
                raise Fault("REFERENCE_LIMIT", "Reference snapshot exceeds the bounded static-analysis channel")
            data = path.read_bytes()
            total += len(data)
            if total > min(settings.max_object_bytes // 2, 8 * 1024 * 1024):
                raise Fault("REFERENCE_LIMIT", "Reference changed while snapshotting")
            content[relative] = entry(data)
    if not content:
        raise Fault("REFERENCE_EMPTY", "Reference directory has no admitted files")
    validate_manifest(content)
    return content


def register(s, tenant, body):
    db.get(s, db.Project, tenant, body.project_id)
    content = capture(body.path)
    data = {
        "kind": "reference_target", "project_id": body.project_id, "label": body.label,
        "snapshot_ref": objects.put(tenant, "reference-targets", content), "snapshot_digest": digest(content),
        "coverage": {"excluded_directories": sorted(DENIED | {".venv"}), "files": len(content)},
        "effects": {"target_execution": False, "network": False, "target_mutation": False}}
    key = "reference-" + digest(data)[7:47]
    existing = s.get(db.PolicyVersion, (tenant, key))
    if existing:
        return target_view(existing)
    row = db.PolicyVersion(tenant_id=tenant, id=key, data=data)
    s.add(row)
    s.flush()
    return target_view(row)


def target_view(row):
    return {"id": row.id, "status": row.status,
            **{k: row.data[k] for k in ("project_id", "label", "snapshot_digest", "coverage", "effects")}}


def pin(s, tenant, actor, spec, parent, admin):
    if not spec.reference_targets:
        return [], None
    if "reference.read" not in spec.capabilities:
        raise Fault("REFERENCE_CAPABILITY", "Select reference.read explicitly", 403)
    inherited = {value["id"] for value in parent.state.get("reference_targets", [])} if parent else None
    values = []
    for key in dict.fromkeys(spec.reference_targets):
        row = db.get(s, db.PolicyVersion, tenant, key)
        if row.data.get("kind") != "reference_target" or row.status != "active" or row.data["project_id"] != spec.project_id:
            raise Fault("REFERENCE_SCOPE", "Reference must be active and registered to this project", 403)
        if inherited is not None and key not in inherited:
            raise Fault("REFERENCE_SCOPE", "Child reference scope exceeds the parent", 403)
        if not parent and not project_access(s, Identity(tenant, actor, admin), spec.project_id, "read"):
            raise Fault("REFERENCE_SCOPE", "Project read access is required", 403)
        values.append({"id": key, "digest": digest(row.data), "data": row.data})
    return values, runtime_binding()


def current(s, run, target_id):
    pinned = next((item for item in run.state.get("reference_targets", []) if item["id"] == target_id), None)
    row = db.get(s, db.PolicyVersion, run.tenant_id, target_id)
    if not pinned or row.status != "active" or digest(row.data) != pinned["digest"]:
        raise Fault("REFERENCE_CHANGED", "Reference is unselected, disabled or changed", 403)
    return pinned


def source(s, run, action_id):
    action = db.get(s, db.Action, run.tenant_id, action_id)
    if action.run_id != run.id or action.tool != "rea.analyze" or action.status != "SUCCEEDED":
        raise Fault("REA_EVIDENCE_SCOPE", "Select a completed analysis from this task", 403)
    raw = json.loads(objects.get(run.tenant_id, action.receipt["ref"]))
    if raw["binding"] != diagnosis.binding(run):
        raise Fault("REA_EVIDENCE_STALE", "Reanalyze for the current task/workspace revision")
    current(s, run, raw["target_id"])
    return raw


def validate_action(s, run, tool, args):
    if not run.state.get("reference_targets"):
        raise Fault("REFERENCE_SCOPE", "No reference target is pinned to this task", 403)
    if tool == "rea.analyze":
        current(s, run, args["target_id"])
    else:
        source(s, run, args["action_id"])


def cli(command, value, directory, timeout, cancelled):
    node = shutil.which("node")
    runtime = settings.rea_runtime_dir
    if not node or not runtime:
        raise Fault("REA_UNAVAILABLE", "Pinned local analyzer is unavailable")
    argv = [node, "--max-old-space-size=384", str(runtime.resolve() / "node_modules/rea-agents/scripts/rea.mjs"),
            command, str(value), "--json"]
    env = {key: value for key, value in os.environ.items() if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH"}}
    env.update(HOME=str(directory), USERPROFILE=str(directory), TEMP=str(directory), TMP=str(directory), NO_COLOR="1")
    data = processes.run(argv, directory, timeout, min(settings.max_object_bytes, 16 * 1024 * 1024), cancelled, env)
    try:
        evidence = json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise Fault("REA_PROTOCOL", "REA did not return complete JSON Evidence") from exc
    if not isinstance(evidence, dict) or not isinstance(evidence.get("normalized_result"), dict) or not evidence.get("evidence_id"):
        raise Fault("REA_PROTOCOL", "REA output is not an Evidence record")
    return evidence


async def execute(tenant, run_id, epoch, owner, action_id, attempt_id, tool, args):
    from . import service

    args = TOOL_INPUTS[tool].model_validate(args).model_dump()
    async with admission("rea-static", 1) as admitted:
        if not admitted:
            raise Fault("REA_BACKPRESSURE", "Static analyzer capacity is occupied")
        with db.transaction(tenant) as s:
            run = db.fence(s, tenant, run_id, owner, epoch)
            service.authorization(s, run, "reference.read")
            validate_action(s, run, tool, args)
            pinned_runtime, bound = run.state["semantic"]["rea_runtime"], diagnosis.binding(run)
            parent = None if tool == "rea.analyze" else source(s, run, args["action_id"])
            target_id = args["target_id"] if parent is None else parent["target_id"]
            target = current(s, run, target_id)
        if await asyncio.to_thread(runtime_binding) != pinned_runtime:
            raise Fault("REA_RUNTIME_DRIFT", "REA or Node differs from the pinned task runtime")
        with db.transaction(tenant) as s:
            run = db.fence(s, tenant, run_id, owner, epoch)
            service.authorization(s, run, "reference.read")
            current(s, run, target_id)
            if run.cancel_requested or run.pause_requested or diagnosis.binding(run) != bound:
                raise Fault("REA_EVIDENCE_STALE", "Control or task revision changed before analyzer start")
        # This host runner has no one-core container guarantee: conservatively reserve all host CPUs.
        cores = os.cpu_count() or 1
        charged = await asyncio.to_thread(resources.admit_cpu, tenant, run_id, attempt_id, 120 * cores)
        timeout = max(0.1, charged / cores)
        cancelled = threading.Event()
        def work():
            started = time.monotonic()
            with tempfile.TemporaryDirectory(prefix="forge-rea-") as temporary:
                directory = Path(temporary)
                if parent is None:
                    snapshot = json.loads(objects.get(tenant, target["data"]["snapshot_ref"]))
                    if digest(snapshot) != target["data"]["snapshot_digest"]:
                        raise Fault("REFERENCE_INTEGRITY", "Reference snapshot digest mismatch")
                    reference = directory / "reference"
                    reference.mkdir()
                    write_tree(reference, snapshot)
                    raw = cli("analyze-javascript-application", reference, directory, timeout, cancelled)
                    raw_ref = resources.put(tenant, run_id, raw)
                    # Persist the complete native Evidence before requesting the compact view.
                    with db.transaction(tenant) as s:
                        db.fence(s, tenant, run_id, owner, epoch)
                        attempt = db.get(s, db.Attempt, tenant, attempt_id)
                        attempt.data = {**attempt.data, "rea_evidence_ref": raw_ref, "binding": bound}
                    request = {"source": {"kind": "inline", "evidence": raw}, "view": {"kind": "summary"}}
                    command = "inspect-analysis-view"
                else:
                    raw_ref = parent["evidence_ref"]
                    raw = json.loads(objects.get(tenant, raw_ref))
                    request = ({"source": {"kind": "inline", "evidence": raw},
                                "view": {"kind": "page", "collection": args["collection"], "offset": args["offset"], "limit": args["limit"]}}
                               if tool == "rea.view" else {"application": raw, "seed": {"kind": args["kind"], "value": args["value"]},
                                                          "direction": args["direction"]})
                    command = "inspect-analysis-view" if tool == "rea.view" else "trace-application-feature"
                request_path = directory / "request.json"
                request_path.write_text(json.dumps(request), encoding="utf-8")
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise Fault("REA_TIMEOUT", "Analysis consumed the bounded execution time")
                view = cli(command, request_path, directory, remaining, cancelled)
                return {"exit_code": 0, "target_id": target_id, "binding": bound, "runtime": pinned_runtime,
                        "source_digest": digest([target["data"]["snapshot_digest"], tool,
                            {key: value for key, value in args.items() if key not in {"action_id", "target_id"}}]),
                        "evidence_ref": raw_ref, "evidence_id": raw["evidence_id"], "view_evidence": view,
                        "limitations": ["Static syntax does not prove runtime behavior or offline search."]}
        task = asyncio.create_task(asyncio.to_thread(work))
        try:
            result = await asyncio.shield(task)
        finally:
            cancelled.set()
            if not task.done():
                try:
                    await asyncio.shield(task)
                except Fault:
                    pass
        # Keep the conservative CPU reservation: process-family CPU time is not separately measured.
        await asyncio.to_thread(resources.settle_cpu, tenant, run_id, attempt_id, {})
        with db.transaction(tenant) as s:
            run = db.fence(s, tenant, run_id, owner, epoch)
            service.authorization(s, run, "reference.read")
            current(s, run, target_id)
            if run.cancel_requested or run.pause_requested or diagnosis.binding(run) != bound:
                raise Fault("REA_EVIDENCE_STALE", "Control or task revision changed during analysis")
        return result
