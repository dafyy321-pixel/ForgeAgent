import os

import pytest
from fastapi.testclient import TestClient
from forgeagent import db, service
from forgeagent.api import app
from forgeagent.auth import Identity, identity
from forgeagent.config import Settings, settings
from forgeagent.domain import CreateRun, Task, uid


@pytest.fixture(autouse=True)
def isolate_model_configuration(monkeypatch, request):
    # Ordinary tests must neither inherit paid credentials nor depend on a
    # developer's streaming/token-count flags. Live release tests opt in.
    if os.environ.get("FORGE_TEST_RELEASE") == "1" and request.module.__name__.endswith("test_release_integration"):
        return
    clean = Settings(_env_file=None, _env_prefix="FORGE_ISOLATED_UNIT_")
    for field in type(settings).model_fields:
        if field.startswith("model_") or field.endswith("_price") or field in {"context_window", "max_output"}:
            monkeypatch.setattr(settings, field, getattr(clean, field))


@pytest.fixture
def tenant():
    value = "test-" + uid()
    service.provision(value, "tester")
    return value


@pytest.fixture
def client(tenant):
    app.dependency_overrides[identity] = lambda: Identity(tenant, "tester", True)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def make_run(tenant):
    def create(**kwargs):
        payload = {"project_id": "runtime-lab", "model": "fixture", "task": Task(goal="Fix add", allowed_paths=["src"])}
        payload.update(kwargs)
        with db.transaction(tenant) as s:
            r = service.create_run(s, tenant, "tester", CreateRun(**payload), uid())
            return r.id

    return create
