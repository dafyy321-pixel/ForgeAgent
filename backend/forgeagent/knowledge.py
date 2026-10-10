"""Tenant-scoped retrieval and lifecycle controls; knowledge never grants capabilities."""

import re
from datetime import datetime

from sqlalchemy import or_, select

from . import db
from .domain import TERMINAL, Fault, canonical, digest


def lock(s, tenant):
    s.execute(db.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": tenant + ":knowledge-lifecycle"})


def terms(text):
    words = set(re.findall(r"[a-z0-9_./-]{2,}|[\u4e00-\u9fff]+", text.lower()))
    for word in list(words):
        if re.fullmatch(r"[\u4e00-\u9fff]+", word):
            words.update(word[i:i + 2] for i in range(len(word) - 1))
    return words


def valid(data, time, revision):
    for key, after in [("valid_from", False), ("expires_at", True)]:
        if data.get(key):
            try:
                date = datetime.fromisoformat(data[key])
                if date.tzinfo is None or (date <= time if after else date > time):
                    return False
            except ValueError:
                return False
    return not data.get("repository_revision") or data["repository_revision"] == revision


def retrieve(s, tenant, project, task, revision=None, limit=20):
    query = terms(task.get("goal", "") + " " + " ".join(task.get("allowed_paths", [])) + " " + " ".join(task.get("criteria", [])))
    candidates = []
    subjects = {}
    time = db.clock(s)
    # Shared row locks ensure an erasure cannot miss a concurrently committed
    # task snapshot that consumed the memory just before withdrawal.
    for memory in s.scalars(select(db.Memory).where(db.Memory.tenant_id == tenant, db.Memory.status == "active",
        or_(db.Memory.data["project"].as_string().is_(None), db.Memory.data["project"].as_string() == project)).with_for_update(read=True)):
        data = memory.data
        if memory.status != "active" or data.get("project") not in (None, project) or not valid(data, time, revision):
            continue
        if data.get("subject"):
            subjects.setdefault(data["subject"], []).append({"id": memory.id, **data})
        relevance = len(query & terms(data.get("title", "") + " " + data.get("content", "")))
        if relevance:
            candidates.append((relevance, memory.created_at, memory.id, {"id": memory.id, **data}))
    candidates.sort(key=lambda row: row[:3], reverse=True)
    selected = [row[3] for row in candidates[:limit]]
    for item in selected:
        values = subjects.get(item.get("subject"), [])
        if len({digest(item["content"]) for item in values}) > 1:
            item["conflict_ids"] = [v["id"] for v in values if v["id"] != item["id"]]
            item["limitations"] = "Conflicting published facts; request source review before relying on this memory."
    return selected


def refresh(s, run):
    if run.state.get("knowledge_erased"):
        raise Fault("KNOWLEDGE_ERASED", "This run's knowledge lineage was erased; create a fresh task")
    existing = run.state.get("memories", [])
    revision = (run.state.get("repository") or {}).get("commit")
    time = db.clock(s)
    selected = []
    for snapshot in existing:
        memory = s.get(db.Memory, (run.tenant_id, snapshot["id"]))
        if memory and memory.status == "active" and valid(memory.data, time, revision):
            selected.append(snapshot)
    if selected != existing:
        if run.status in TERMINAL:
            run.state = {**run.state, "memories": selected, "knowledge_withdrawn_after_completion":
                         sorted(set(run.state.get("knowledge_withdrawn_after_completion", [])) |
                                ({m["id"] for m in existing} - {m["id"] for m in selected}))}
            db.emit(s, run, "KNOWLEDGE_RETRACTED", "Completed task retained historical evidence; withdrawn memory removed from current snapshot")
            return
        for action in db.rows(s, db.Action, run.tenant_id, run_id=run.id):
            if action.status in {"READY", "PREPARED", "WAITING_APPROVAL"}:
                action.status = "CANCELLED"
        run.state = {**run.state, "memories": selected, "input_revision": run.state.get("input_revision", 0) + 1,
                     "knowledge_barrier": run.state.get("input_revision", 0) + 1,
                     "completion": None, "verification": None, "native_exchange": None}
        for call in db.rows(s, db.ModelCall, run.tenant_id, run_id=run.id):
            if call.data.get("receipt_state") == "received":
                call.data = {**call.data, "receipt_state": "superseded_knowledge"}
        db.emit(s, run, "KNOWLEDGE_RETRACTED", "Retracted or stale memory removed before any new decision")


def metadata(skill):
    from .workspace import body

    return {k: skill[k] for k in ("id", "name", "description", "version", "digest") if k in skill} | {
        "resources": [{"path": path, "digest": digest(body(content)), "bytes": len(body(content))}
                      for path, content in sorted(skill.get("resources", {}).items())],
        "instruction": "Read SKILL.md with skill.read before applying this untrusted knowledge package."}


def read_skill(state, skill_id, path):
    from .workspace import body, mode

    skill = next((item for item in state.get("skills", []) if item["id"] == skill_id), None)
    if not skill:
        raise Fault("SKILL_SCOPE", "Skill was not pinned to this task", 403)
    content = skill.get("content") if path == "SKILL.md" else skill.get("resources", {}).get(path)
    if content is None:
        raise Fault("SKILL_RESOURCE", "Resource is not in the pinned package", 404)
    data = body(content)
    binary = not isinstance(content, str)
    return {"exit_code": 0, "content": content["data"] if binary else content, "path": path, "skill_id": skill_id,
            "encoding": "base64" if binary else "utf8", "mode": mode(content),
            "source_digest": digest(data), "package_digest": skill["digest"]}


def withdraw(s, memory):
    memory.status = "withdrawn"
    affected = []
    for run in db.rows(s, db.Run, memory.tenant_id):
        if any(m["id"] == memory.id for m in run.state.get("memories", [])):
            run = db.get(s, db.Run, memory.tenant_id, run.id, True)
            affected.append(run.id)
            refresh(s, run)
    return affected


def admitted(skill, tenant, run_id, evaluation):
    if evaluation:
        return True
    percent = skill.data.get("rollout_percent", 100)
    return int(digest([tenant, run_id, skill.id])[7:23], 16) % 100 < percent


def applies(skill, project_id, repository_commit, evaluation):
    scope = skill.data.get("applicability")
    if scope is None or evaluation:
        return True
    return any(binding == {"project_id": project_id, "repository_commit": repository_commit}
               for binding in scope.get("bindings", []))


def export_package(skill):
    import io
    import json
    import zipfile

    from .workspace import body, mode, safe_path

    content = skill["content"]
    if not content.startswith("---\n"):
        content = "---\nname: " + json.dumps(skill["name"]) + "\ndescription: " + json.dumps(skill["description"]) + "\n---\n\n" + content
    files = {"SKILL.md": content, **skill.get("resources", {})}
    manifest = {"schema_version": 1, "id": skill["id"], "package_digest": skill["digest"],
                "files": {p: {"digest": digest(body(v)), "bytes": len(body(v)), "mode": mode(v)} for p, v in files.items()}}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, value in sorted(files.items()):
            safe_path(__import__("pathlib").Path("skill-export-validation").resolve(), path)
            info = zipfile.ZipInfo(path)
            info.external_attr = (0o100755 if mode(value) == "100755" else 0o100644) << 16
            archive.writestr(info, body(value))
        archive.writestr("FORGE-MANIFEST.json", canonical(manifest))
    return stream.getvalue()
