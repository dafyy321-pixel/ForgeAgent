import json

import pytest
from fastapi.testclient import TestClient
from forgeagent import db, sandbox_manager
from forgeagent.config import settings
from forgeagent.domain import canonical, uid
from forgeagent.sandbox import sandbox


@pytest.mark.parametrize("legacy_completed", [False, True])
def test_purge_follows_copied_workspaces_and_dialogue(tenant, make_run, client, legacy_completed):
    source, fork, discussion, execution, unrelated = [make_run() for _ in range(5)]
    with db.transaction(tenant) as s:
        if legacy_completed:
            s.add(db.PolicyVersion(tenant_id=tenant, id="run-erasure-" + source, status="completed",
                                   data={"runs": [source], "reviewer": "tester", "memory_id": None}))
        for run_id, field, origin in [(fork, "forked_from", source), (discussion, "source_run_id", fork),
                                      (execution, "source_discussion_id", discussion)]:
            run = db.get(s, db.Run, tenant, run_id)
            run.state = {**run.state, field: origin, "dialogue": [{"role": "user", "content": "copied material"}]}

    result = client.post(f"/v1/operations/runs/{source}/purge")
    assert result.status_code == 200, result.text
    assert set(result.json()["derived_runs"]) == {source, fork, discussion, execution}
    assert client.get(f"/v1/runs/{execution}/conversation").json()["followup_reference"] is None
    with db.transaction(tenant) as s:
        assert "copied material" not in json.dumps(db.get(s, db.Run, tenant, execution).state)
        assert not db.get(s, db.Run, tenant, unrelated).state.get("knowledge_erased")


def test_purge_removes_current_and_legacy_receipts(tenant, make_run, client, tmp_path, monkeypatch):
    source, other = make_run(), make_run()
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "sandbox_manager_secret", "s" * 32)
    directory = tmp_path / "sandbox-manager"

    async def fake_execution(body):
        return {"exit_code": 0, "output": body.argv[-1]}

    monkeypatch.setattr(sandbox_manager, "run_execution", fake_execution)
    with TestClient(sandbox_manager.app) as manager:
        for run_id, content in [(source, "erased material"), (other, "retained material")]:
            workspace = sandbox.root(tenant, run_id, 1)
            workspace.mkdir(parents=True)
            payload = sandbox_manager.Execution(operation=uid(), root=str(workspace), argv=["example", content],
                image="sha256:" + "a" * 64, timeout=5).model_dump()
            auth = {"Authorization": "Bearer " + sandbox_manager.token("execute", payload)}
            assert manager.post("/execute", headers=auth, json=payload).json()["output"] == content
            legacy = directory / uid()
            legacy.mkdir()
            (legacy / "request.json").write_bytes(canonical({**payload, "operation": legacy.name}))
            (legacy / "receipt.json").write_bytes(canonical({"output": content}))
            if run_id == other:
                retained = legacy
            else:
                erased_payload, erased_auth = payload, auth

    result = client.post(f"/v1/operations/runs/{source}/purge")
    assert result.status_code == 200, result.text
    assert not any(b"erased material" in path.read_bytes() for path in directory.rglob("*") if path.is_file())
    assert json.loads((retained / "receipt.json").read_bytes())["output"] == "retained material"
    with TestClient(sandbox_manager.app) as manager:
        replay = manager.post("/execute", json=erased_payload, headers=erased_auth)
        assert replay.status_code == 410 and replay.json()["code"] == "MANAGER_RECEIPT_ERASED"
