"""Real public REA CLI integration and scope/supervision regressions."""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest
from forgeagent import db, processes, progress, rea, service
from forgeagent.api import app
from forgeagent.auth import Identity, identity
from forgeagent.config import settings
from forgeagent.domain import Fault, ToolCall
from forgeagent.storage import objects
from forgeagent.worker import Worker

RUNTIME = Path(__file__).resolve().parents[2] / ".forge/rea"


def test_normal_analyzer_exit_reaps_descendants_before_directory_cleanup(tmp_path):
    marker = tmp_path / "orphan-marker"
    child = "import time,pathlib;time.sleep(.5);pathlib.Path(" + repr(str(marker)) + ").write_text('orphan')"
    parent = ("import subprocess,sys;subprocess.Popen([sys.executable,'-c'," + repr(child)
              + "],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);print('complete')")
    for _ in range(3):
        with tempfile.TemporaryDirectory(prefix="forge-rea-exit-") as temporary:
            assert processes.run([sys.executable, "-c", parent], Path(temporary), 5, 1024,
                                 threading.Event(), os.environ.copy()).strip() == b"complete"
        assert not Path(temporary).exists()
    threading.Event().wait(.6)
    assert not marker.exists()


@pytest.fixture
def reference(tmp_path, tenant, monkeypatch):
    target = tmp_path / "reference"
    target.mkdir()
    (target / "package.json").write_text('{"name":"search-reference","main":"main.js"}', encoding="utf-8")
    (target / "main.js").write_text('require("fs").writeFileSync(' + json.dumps(str(target / "EXECUTED")) + ', "bad");\n'
        'module.exports = {search: q => ["offline-search", "local"].filter(x => x.includes(q))};', encoding="utf-8")
    monkeypatch.setattr(settings, "rea_target_roots", json.dumps([str(tmp_path)]))
    with db.transaction(tenant) as s:
        row = rea.register(s, tenant, rea.RegisterTarget(project_id="runtime-lab", label="Search reference", path=str(target)))
    return row["id"], target


def test_reference_snapshot_is_fixed_and_host_path_is_not_exposed(tenant, reference, client):
    target_id, target = reference
    (target / "main.js").write_text("changed", encoding="utf-8")
    with db.transaction(tenant) as s:
        row = db.get(s, db.PolicyVersion, tenant, target_id)
        content = json.loads(objects.get(tenant, row.data["snapshot_ref"]))
        assert "offline-search" in content["main.js"]
    result = client.get("/v1/reference-targets?project_id=runtime-lab")
    assert result.status_code == 200 and str(target) not in result.text
    app.dependency_overrides[identity] = lambda: Identity(tenant, "outsider", False)
    assert client.get("/v1/reference-targets?project_id=runtime-lab").status_code == 403
    assert client.post("/v1/reference-targets", json={"project_id": "runtime-lab", "label": "bad", "path": str(target)}).status_code == 403


def test_reference_escape_and_unpinned_target_are_rejected(tmp_path, monkeypatch, tenant, make_run, reference):
    monkeypatch.setattr(settings, "rea_target_roots", json.dumps([str(tmp_path / "allowed")]))
    with pytest.raises(Fault, match="operator-configured"):
        rea.capture(str(tmp_path))
    run_id = make_run(capabilities=["repo.read", "reference.read"])
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        action = service.prepare_action(s, run, ToolCall(tool="rea.analyze", args={"target_id": reference[0]}), "scope")
        assert action.status == "FAILED" and "No reference" in action.receipt["error"]
    link = tmp_path / "link"
    try:
        link.symlink_to(reference[1], target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation unavailable")
    monkeypatch.setattr(settings, "rea_target_roots", json.dumps([str(tmp_path)]))
    with pytest.raises(Fault):
        rea.capture(str(link))


def test_owned_process_timeout_and_output_bound(tmp_path):
    stop = threading.Event()
    started = time.monotonic()
    with pytest.raises(Fault, match="deadline"):
        processes.run([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, .2, 1024, stop, os.environ.copy())
    assert time.monotonic() - started < 5
    with pytest.raises(Fault, match="Evidence|output"):
        processes.run([sys.executable, "-c", "print('x'*10000)"], tmp_path, 5, 1024, stop, os.environ.copy())


def test_timeout_closes_owned_descendants(tmp_path):
    marker = tmp_path / "descendant-survived"
    child = "import time,pathlib;time.sleep(1);pathlib.Path(" + repr(str(marker)) + ").write_text('bad')"
    parent = "import subprocess,sys,time;subprocess.Popen([sys.executable,'-c'," + repr(child) + "]);time.sleep(30)"
    with pytest.raises(Fault, match="deadline"):
        processes.run([sys.executable, "-c", parent], tmp_path, .2, 1024, threading.Event(), os.environ.copy())
    threading.Event().wait(1.1)
    assert not marker.exists()


def test_repeated_static_evidence_does_not_reset_progress(tenant, make_run):
    run_id = make_run()
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        progress.observed(s, run, "rea.analyze", {"source_digest": "stable-selection", "evidence_id": "first"}, "SUCCEEDED")
        run.state = {**run.state, "progress": {**run.state["progress"], "stalled_turns": 4}}
        progress.observed(s, run, "rea.analyze", {"source_digest": "stable-selection", "evidence_id": "new-process-id"}, "SUCCEEDED")
        assert run.state["progress"]["stalled_turns"] == 4


async def test_supervised_work_cancellation_waits_for_cleanup(tmp_path):
    stop = threading.Event()
    work = asyncio.create_task(asyncio.to_thread(processes.run,
        [sys.executable, "-c", "import time;time.sleep(30)"], tmp_path, 20, 1024, stop, os.environ.copy()))
    await asyncio.sleep(.15)
    stop.set()
    with pytest.raises(Fault, match="cancelled"):
        await asyncio.wait_for(work, 5)


@pytest.mark.skipif(not (RUNTIME / "node_modules/rea-agents/scripts/rea.mjs").exists() or not shutil.which("node"),
                    reason="Install pinned rea-agents@6.4.0 in .forge/rea for real CLI verification")
async def test_real_rea_analysis_view_trace_and_stale_evidence(tenant, make_run, reference, monkeypatch, client):
    monkeypatch.setattr(settings, "rea_runtime_dir", RUNTIME)
    run_id = make_run(reference_targets=[reference[0]], capabilities=["repo.read", "reference.read"])
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        action = service.prepare_action(s, run, ToolCall(tool="rea.analyze", args={"target_id": reference[0]}), "analyze")
        analysis_id = action.id
    worker = Worker(target_run=run_id)
    await worker.once(tenant)
    with db.transaction(tenant) as s:
        action = db.get(s, db.Action, tenant, analysis_id)
        assert action.status == "SUCCEEDED", action.receipt
        receipt = json.loads(objects.get(tenant, action.receipt["ref"]))
        raw = json.loads(objects.get(tenant, receipt["evidence_ref"]))
        assert raw["evidence_id"] == receipt["evidence_id"]
        assert raw["authority"] == "shipped-artifact" and raw["confidence"] == "derived"
        summary = receipt["view_evidence"]["normalized_result"]
        assert summary["unknowns"] and summary["summary"]["coverage"]
        assert db.rows(s, db.EvidenceRecord, tenant, run_id=run_id)
        assert not (reference[1] / "EXECUTED").exists()
        run = db.get(s, db.Run, tenant, run_id)
        view = service.prepare_action(s, run, ToolCall(tool="rea.view", args={"action_id": analysis_id,
            "collection": "modules", "offset": 0, "limit": 1}), "view")
        trace = service.prepare_action(s, run, ToolCall(tool="rea.trace", args={"action_id": analysis_id,
            "kind": "string", "value": "offline-search", "direction": "both"}), "trace")
        view_id, trace_id = view.id, trace.id
    exported = client.get(f"/v1/runs/{run_id}/reference-evidence/{analysis_id}")
    assert exported.status_code == 200 and exported.json() == raw
    other = make_run()
    assert client.get(f"/v1/runs/{other}/reference-evidence/{analysis_id}").status_code == 403
    await worker.once(tenant)
    with db.transaction(tenant) as s:
        for key in (view_id, trace_id):
            action = db.get(s, db.Action, tenant, key)
            assert action.status == "SUCCEEDED", action.receipt
            receipt = json.loads(objects.get(tenant, action.receipt["ref"]))
            assert json.loads(objects.get(tenant, receipt["evidence_ref"])) == raw
        run = db.get(s, db.Run, tenant, run_id)
        run.state = {**run.state, "input_revision": 1}
        stale = service.prepare_action(s, run, ToolCall(tool="rea.trace", args={"action_id": analysis_id,
            "value": "offline-search"}), "stale")
        assert stale.status == "FAILED" and "current task/workspace" in stale.receipt["error"]
    assert client.post(f"/v1/reference-targets/{reference[0]}/disable").status_code == 200
    with db.transaction(tenant) as s:
        with pytest.raises(Fault, match="disabled"):
            rea.current(s, db.get(s, db.Run, tenant, run_id), reference[0])
