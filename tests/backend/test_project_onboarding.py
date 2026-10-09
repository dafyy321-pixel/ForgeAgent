import base64
import hashlib

from forgeagent import db
from forgeagent.api import app
from forgeagent.auth import Identity, identity
from forgeagent.config import settings
from test_repository import make_repository


def upload(client, body):
    return client.put("/v1/repository-bundles/" + hashlib.sha256(body).hexdigest(), content=body,
                      headers={"Content-Type": "application/octet-stream"})


def test_uploaded_git_bundle_preflight_and_registration_are_digest_bound(client, tenant, tmp_path):
    spec = make_repository(tmp_path / "repo")
    body = base64.b64decode(spec.bundle_base64)
    receipt = upload(client, body)
    assert receipt.status_code == 200
    assert upload(client, body).json() == receipt.json()
    project = {"id": "uploaded", "name": "uploaded", "repository": {"commit": spec.commit, **receipt.json()},
               "acceptance_id": "check@1", "verification_argv": ["python", "-m", "unittest"],
               "protected_tests": {"hidden_tests/test_private.py": "SECRET_TEST_BODY"}}
    report = client.post("/v1/projects/preflight", json=project)
    assert report.status_code == 200 and report.json()["acceptance_execution"] == "not_run"
    assert "SECRET_TEST_BODY" not in report.text
    with db.transaction(tenant) as s:
        assert s.get(db.Project, (tenant, "uploaded")) is None
    assert client.post("/v1/projects", json=project).status_code == 201
    forged = {**project, "id": "forged", "repository": {**project["repository"], "bundle_bytes": len(body) + 1}}
    assert client.post("/v1/projects/preflight", json=forged).status_code == 409
    app.dependency_overrides[identity] = lambda: Identity(tenant, "tester", False)
    assert upload(client, body).status_code == 403
    assert client.post("/v1/projects/preflight", json=project).status_code == 403


def test_raw_upload_exceeds_json_limit_but_is_bounded_without_content_length(client, monkeypatch):
    body = b"x" * (2 * 1024 * 1024 + 1)
    assert upload(client, body).status_code == 200
    assert client.post("/v1/projects", content=body).status_code == 413
    path = "/v1/repository-bundles/" + hashlib.sha256(b"x").hexdigest()
    assert client.put(path, content=b"wrong", headers={"Content-Type": "application/octet-stream"}).status_code == 422
    monkeypatch.setattr(settings, "max_object_bytes", 10)
    response = client.put(path, content=iter([b"123456", b"123456"]), headers={"Content-Type": "application/octet-stream"})
    assert response.status_code == 413
