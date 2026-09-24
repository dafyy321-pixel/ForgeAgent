import pytest
from fastapi.testclient import TestClient
from forgeagent import db, service
from forgeagent.api import app
from forgeagent.auth import Identity, identity
from forgeagent.domain import CreateRun, Task, uid


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
