import json
from datetime import timedelta

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from forgeagent import db, resources, sandbox_manager, service
from forgeagent.config import settings
from forgeagent.domain import AcceptanceContract, Budget, BuildContract, CreateRun, Fault, Task, digest, uid
from forgeagent.sandbox import sandbox
from forgeagent.verification import verify
from forgeagent.workspace import entry, validate_manifest


@pytest.mark.parametrize("manifest", [
    {"a": entry(b"b", "120000"), "b": entry(b"a", "120000")},
    {"a": entry(b".", "120000"), "b": entry(b"a/../outside", "120000")},
    {"a": entry(b"x\\..\\secret", "120000")}, {"a": entry(b"\xff", "120000")},
])
def test_symlink_graph_validation_precedes_materialization(manifest):
    with pytest.raises(Fault):
        validate_manifest(manifest)


def test_contracts_reject_unlocked_dependencies_and_unknown_delivery():
    with pytest.raises(ValueError):
        BuildContract(ecosystem="node", lockfile="package-lock.json")
    with pytest.raises(ValueError):
        AcceptanceContract(id="missing-command")
    with pytest.raises(ValueError):
        Task(goal="publish", deliverables=["whatever"])


async def test_independent_build_conditions_and_evidence(tenant, monkeypatch):
    calls = []
    image = "sha256:" + "a" * 64
    current = {"src/main.py": "print('ok')", "uv.lock": "locked"}
    build = BuildContract(ecosystem="python", lockfile="uv.lock", lock_digest=digest(b"locked"),
                          dependency_image=image, argv=["python", "-m", "compileall", "src"], persistent_session=True).model_dump()
    contract = AcceptanceContract(id="python@1", argv=["python", "src/main.py"], conditions=[
        {"kind": "file_digest", "path": "src/main.py", "value": digest(b"print('ok')")},
        {"kind": "output_contains", "value": "ok"}]).model_dump()
    async def fake(root, argv, image, timeout, readonly, build):
        calls.append((argv, readonly, build["persistent_session"]))
        return {"exit_code": 0, "output": "ok", "elapsed_seconds": 0.1}
    report = await verify(tenant, uid(), 1, current, current, contract, ["src"], image, build=build,
                          executor=fake, metadata={"task_id": "fixed-task", "input_revision": 3})
    assert report["verdict"] == "PASS" and len(calls) == 1
    assert all(readonly and not persistent for _, readonly, persistent in calls)
    assert report["evidence_version"] == 2 and report["started_at"] <= report["completed_at"]
    assert report["task_binding"]["input_revision"] == 3
    assert report["dependencies"]["lock_digest"] == digest(b"locked")
    contract["conditions"][1]["value"] = "missing"
    report = await verify(tenant, uid(), 2, current, current, contract, ["src"], image, build=build, executor=fake)
    assert report["verdict"] == "FAIL" and report["failure_class"] == "acceptance"


async def test_configuration_injection_fails_before_sandbox(tenant):
    async def never(*args, **kwargs):
        pytest.fail("Protected configuration must fail before executing generated code")
    report = await verify(tenant, uid(), 1, {"src/main.py": "ok"}, {"src/main.py": "ok", "conftest.py": "skip everything"},
                          {"argv": ["pytest"]}, ["src", "conftest.py"], "image", executor=never)
    assert report["verdict"] == "FAIL" and report["failure_class"] == "hard_constraint"


async def test_environment_failures_remain_inconclusive_and_private(tenant):
    async def unavailable(*args, **kwargs):
        raise Fault("SANDBOX_UNAVAILABLE", "unavailable")
    report = await verify(tenant, uid(), 1, {}, {}, {"argv": ["pytest"], "protected_tests": {"tests/secret.py": "SECRET"}},
                          ["src"], "image", executor=unavailable)
    assert report["verdict"] == "INCONCLUSIVE" and report["failure_class"] == "environment"
    assert "SECRET" not in json.dumps(report) and report["result"]["protected_output"]


@pytest.mark.parametrize("exit_code", [2, 3, 4, 5, 125, 126, 127, 137])
async def test_unregistered_or_environment_exit_is_not_business_failure(tenant, exit_code):
    async def result(*args, **kwargs):
        return {"exit_code": exit_code, "output": "runner could not determine acceptance"}
    report = await verify(tenant, uid(), 1, {}, {}, {"argv": ["pytest"]}, ["src"], "image", executor=result)
    assert report["verdict"] == "INCONCLUSIVE" and report["failure_class"] == "environment"


async def test_explicit_business_exit_classification(tenant):
    async def result(*args, **kwargs):
        return {"exit_code": 3, "output": "application rejected the registered case"}
    contract = AcceptanceContract(id="app@1", argv=["app"], failure_exit_codes=[1, 3]).model_dump()
    report = await verify(tenant, uid(), 1, {}, {}, contract, ["src"], "image", executor=result)
    assert report["verdict"] == "FAIL" and report["failure_class"] == "acceptance"


def test_root_cpu_quota_is_atomic_and_crash_conservative(tenant, make_run):
    run_id = make_run(budget=Budget(max_cpu_seconds=100))
    assert resources.admit_cpu(tenant, run_id, "first", 80) == 80
    with pytest.raises(Fault, match="CPU quota"):
        resources.admit_cpu(tenant, run_id, "second", 30)
    resources.settle_cpu(tenant, run_id, "first", {"elapsed_seconds": 9.2})
    resources.settle_cpu(tenant, run_id, "first", {"elapsed_seconds": 0})
    assert resources.admit_cpu(tenant, run_id, "second", 90) == 90
    with db.transaction(tenant) as s:
        account = db.get(s, db.BudgetAccount, tenant, run_id)
        assert account.resources["cpu_seconds"] == 100
        assert not account.resources["cpu_charges"]["second"]["settled"]


def test_child_execution_consumes_parent_resource_quota(tenant, make_run):
    parent_id = make_run(capabilities=["repo.read", "delegate"], budget=Budget(max_cpu_seconds=20))
    with db.transaction(tenant) as s:
        parent = db.get(s, db.Run, tenant, parent_id)
        child = service.create_run(s, tenant, "tester", CreateRun(project_id="runtime-lab", model="fixture",
            task=Task(goal="child", allowed_paths=["src"]), capabilities=["repo.read"],
            budget=Budget(max_cost_usd="0.01", max_tokens=100, max_cpu_seconds=1000)), uid(), parent=parent)
        child_id = child.id
    resources.admit_cpu(tenant, child_id, "child", 15)
    with pytest.raises(Fault, match="CPU quota"):
        resources.admit_cpu(tenant, parent_id, "parent", 10)


def test_storage_quota_and_duplicate_digest(tenant, make_run):
    run_id = make_run(budget=Budget(max_storage_bytes=2048))
    first = resources.put(tenant, run_id, b"a" * 1000)
    assert resources.put(tenant, run_id, b"a" * 1000) == first
    with pytest.raises(Fault, match="storage|stored"):
        resources.put(tenant, run_id, b"b" * 2000)
    with db.transaction(tenant) as s:
        assert db.get(s, db.BudgetAccount, tenant, run_id).resources["storage_bytes"] < 2048


def test_root_wall_clock_limits_children_and_execution(tenant, make_run):
    run_id = make_run(budget=Budget(max_wall_seconds=10))
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        run.created_at = db.clock(s) - timedelta(seconds=20)
    with pytest.raises(Fault, match="wall-clock"):
        resources.admit_cpu(tenant, run_id, "late", 1)


def test_manager_short_credentials_bind_exact_operation(monkeypatch):
    monkeypatch.setattr(settings, "sandbox_manager_secret", "s" * 32)
    payload = {"route": "registry"}
    authorization = "Bearer " + sandbox_manager.token("credential_read", payload)
    sandbox_manager.authorize(authorization, "credential_read", payload)
    with pytest.raises(Fault, match="exact operation"):
        sandbox_manager.authorize(authorization, "credential_read", {"route": "other"})
    with pytest.raises(Fault):
        sandbox_manager.authorize(authorization, "execute", payload)
    expired = jwt.encode({"aud": "forge-sandbox-manager", "iat": 1, "exp": 2, "action": "credential_read",
                          "digest": digest(payload)}, "s" * 32, algorithm="HS256")
    with pytest.raises(Fault, match="expired"):
        sandbox_manager.authorize(expired, "credential_read", payload)


def test_manager_execution_is_durable_and_idempotent(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "sandbox_manager_secret", "s" * 32)
    calls = []
    async def fake(body):
        calls.append(body.operation)
        return {"exit_code": 0, "output": "ok"}
    monkeypatch.setattr(sandbox_manager, "run_execution", fake)
    payload = sandbox_manager.Execution(operation=uid(), root=str(tmp_path / "workspaces" / "run"),
        argv=["pytest"], image="sha256:" + "a" * 64, timeout=10).model_dump()
    headers = {"Authorization": "Bearer " + sandbox_manager.token("execute", payload)}
    with TestClient(sandbox_manager.app) as client:
        assert client.post("/execute", json=payload, headers=headers).status_code == 200
        assert client.post("/execute", json=payload, headers=headers).status_code == 200
        assert calls == [payload["operation"]]
        payload["argv"] = ["changed"]
        assert client.post("/execute", json=payload, headers=headers).status_code == 403
        headers = {"Authorization": "Bearer " + sandbox_manager.token("execute", payload)}
        assert client.post("/execute", json=payload, headers=headers).status_code == 409


def test_credential_proxy_is_allowlisted_and_never_returns_credential(monkeypatch):
    monkeypatch.setattr(settings, "sandbox_manager_secret", "s" * 32)
    monkeypatch.setattr(settings, "credential_routes", json.dumps({"registry": {
        "url": "https://registry.example.invalid/status", "token_env": "FORGE_TEST_UPSTREAM_TOKEN"}}))
    monkeypatch.setenv("FORGE_TEST_UPSTREAM_TOKEN", "private-upstream-key")
    original = httpx.AsyncClient
    def upstream(request):
        assert request.method == "GET" and request.headers["authorization"] == "Bearer private-upstream-key"
        return httpx.Response(200, content=b"status ok private-upstream-key")
    monkeypatch.setattr(sandbox_manager.httpx, "AsyncClient", lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(upstream)))
    with TestClient(sandbox_manager.app) as client:
        for route, status in [("registry", 200), ("not-approved", 403)]:
            payload = {"route": route}
            response = client.post("/credentials/proxy", json=payload,
                headers={"Authorization": "Bearer " + sandbox_manager.token("credential_read", payload)})
            assert response.status_code == status and "private-upstream-key" not in response.text


async def test_manager_rejects_unmanaged_mount_and_non_gvisor_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    request = sandbox_manager.Execution(operation=uid(), root=str(tmp_path / "outside"),
        argv=["pytest"], image="sha256:" + "a" * 64, timeout=10)
    with pytest.raises(Fault, match="managed workspace"):
        await sandbox_manager.run_execution(request)
    root = tmp_path / "workspaces" / "run"
    root.mkdir(parents=True)
    request.root = str(root)
    monkeypatch.setattr(settings, "sandbox_runtime", "runc")
    with pytest.raises(Fault, match="runsc"):
        await sandbox_manager.run_execution(request)


async def test_shared_worker_has_no_direct_docker_authority(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "sandbox_manager_url", "")
    with pytest.raises(Fault, match="independent|gVisor"):
        await sandbox.execute(tmp_path, ["pytest"], "image")
    with pytest.raises(Fault, match="independent"):
        await sandbox.image_digest()


def test_required_publication_is_not_implied_by_local_verification(tenant, make_run):
    run_id = make_run(task=Task(goal="publish", publication=[{"tool": "deploy", "effect_digest": digest("release")}]))
    with db.transaction(tenant) as s:
        with pytest.raises(Fault, match="business receipt"):
            service.require_finalizable(s, db.get(s, db.Run, tenant, run_id))
