import json

import pytest
from forgeagent import db, service
from forgeagent.domain import Fault, Task, uid
from forgeagent.worker import Worker


def discussion(make_run):
    return make_run(model="configured", capabilities=["repo.read"], task=Task(mode="discuss", goal="Fix add"))


async def test_durable_multiturn_discussion_and_confirmed_execution(tenant, make_run, client):
    run_id = discussion(make_run)
    captured = []
    answers = iter([("request_input", "需要保留小数相加吗？"), ("propose_completion", "修复加法，保留小数并运行独立测试。")])

    async def model(messages, model_id):
        captured.append(messages)
        kind, summary = next(answers)
        return json.dumps({"kind": kind, "summary": summary}), {"input_tokens": 1, "output_tokens": 1}, {}, "local-test"

    await Worker(model=model, target_run=run_id).once(tenant)
    view = client.get(f"/v1/runs/{run_id}").json()
    assert view["status"] == "PAUSED" and view["mode"] == "discuss" and not view["planReady"]
    message = {"content": "需要，使用现有的小数行为。", "expected_version": view["stateVersion"]}
    sent = client.post(f"/v1/runs/{run_id}/messages", json=message, headers={"Idempotency-Key": "reply-1"})
    assert sent.status_code == 200, sent.text
    assert client.post(f"/v1/runs/{run_id}/messages", json=message, headers={"Idempotency-Key": "reply-1"}).status_code == 200
    await Worker(model=model, target_run=run_id).once(tenant)
    history = client.get(f"/v1/runs/{run_id}/conversation").json()
    assert [m["role"] for m in history["messages"]] == ["assistant", "user", "assistant"]
    assert history["plan_ready"] and "小数行为" in json.dumps(captured[-1], ensure_ascii=False)
    view = client.get(f"/v1/runs/{run_id}").json()
    assert client.post(f"/v1/runs/{run_id}/verify", json={"expected_version": view["stateVersion"]}).status_code == 409
    spec = {"project_id": "runtime-lab", "model": "fixture", "task": {"goal": "Fix add", "allowed_paths": ["src"]}}
    confirmation = {"expected_version": view["stateVersion"], "spec": spec}
    started = client.post(f"/v1/runs/{run_id}/execute", json=confirmation, headers={"Idempotency-Key": "execute-1"})
    assert started.status_code == 202, started.text
    execution = started.json()
    assert execution["id"] != run_id and execution["sourceDiscussionId"] == run_id
    again = client.post(f"/v1/runs/{run_id}/execute", json=confirmation, headers={"Idempotency-Key": "execute-1"})
    assert again.json()["id"] == execution["id"]
    with db.transaction(tenant) as s:
        r = db.get(s, db.Run, tenant, run_id)
        assert r.state["workspace_ref"] == r.state["baseline_ref"] and not r.state.get("verification")
        assert not db.rows(s, db.Artifact, tenant, run_id=run_id)
        assert len(db.get(s, db.Run, tenant, execution["id"]).state["dialogue"]) == 3
    worker = Worker(target_run=execution["id"])
    for _ in range(12):
        await worker.once(tenant)
    assert client.get(f"/v1/runs/{execution['id']}").json()["status"] == "SUCCEEDED"


def test_discussion_rejects_write_capabilities_and_fixture_model(make_run):
    with pytest.raises(Fault, match="only project/reference reads"):
        make_run(task=Task(mode="discuss", goal="Inspect"), model="configured")
    with pytest.raises(Fault, match="only project/reference reads"):
        make_run(task=Task(mode="discuss", goal="Inspect"), capabilities=["repo.read"])


async def test_discussion_model_cannot_write_before_confirmation(tenant, make_run):
    run_id = discussion(make_run)

    async def model(*args):
        return json.dumps({"kind": "tool_calls", "summary": "write now", "calls": [{"tool": "repo.write",
            "args": {"path": "src/unapproved.py", "content": "bad", "expected_digest": "absent"}}]}), {
                "input_tokens": 1, "output_tokens": 1}, {}, "local-test"

    await Worker(model=model, target_run=run_id).once(tenant)
    with db.transaction(tenant) as s:
        actions = db.rows(s, db.Action, tenant, run_id=run_id)
        assert len(actions) == 1 and actions[0].status == "FAILED" and actions[0].attempt == 0


def test_messages_require_current_paused_state_and_are_private(tenant, make_run, client):
    run_id = discussion(make_run)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        version = run.version
    response = client.post(f"/v1/runs/{run_id}/messages", json={"content": "hello", "expected_version": version},
                           headers={"Idempotency-Key": uid()})
    assert response.status_code == 409
    from forgeagent.api import app
    from forgeagent.auth import Identity, identity

    app.dependency_overrides[identity] = lambda: Identity(tenant, "outsider")
    assert client.get(f"/v1/runs/{run_id}/conversation").status_code == 403


def test_plan_confirmation_rejects_unready_or_other_projects(tenant, make_run, client):
    run_id = discussion(make_run)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        run.status = "PAUSED"
        run.state = {**run.state, "plan_ready": False}
        db.emit(s, run, "TEST_PAUSE", "pause")
        version = run.version
    payload = {"expected_version": version, "spec": {"project_id": "other", "task": {"goal": "write"}}}
    assert client.post(f"/v1/runs/{run_id}/execute", json=payload, headers={"Idempotency-Key": uid()}).status_code == 409
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        run.state = {**run.state, "plan_ready": True}
        db.emit(s, run, "TEST_READY", "ready")
        payload["expected_version"] = run.version
    assert client.post(f"/v1/runs/{run_id}/execute", json=payload, headers={"Idempotency-Key": uid()}).status_code == 422


def test_message_history_is_bounded(tenant, make_run):
    run_id = discussion(make_run)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        run.state = {**run.state, "dialogue": [{"content": "x" * 60000}]}
        with pytest.raises(Fault, match="Conversation is full"):
            service.append_message(s, run, "user", "one more")
