import time
from unittest.mock import AsyncMock

import httpx
import pytest
from forgeagent import db
from forgeagent.api import app
from forgeagent.auth import Identity, identity
from forgeagent.config import settings
from forgeagent.sdk import Client, ForgeError
from forgeagent.storage import objects
from sqlalchemy import event


def test_keyset_pages_are_stable_and_bound_to_identity_and_filters(client, tenant, make_run):
    ids = [make_run(title="page-" + str(index)) for index in range(5)]
    first = client.get("/v1/runs?limit=2").json()
    assert [run["id"] for run in first["items"]] == ids[-1:-3:-1]
    cursor = first["next_cursor"]
    make_run(title="new arrival")
    second = client.get("/v1/runs", params={"limit": 2, "cursor": cursor}).json()
    assert [run["id"] for run in second["items"]] == ids[-3:-5:-1]
    assert client.get("/v1/runs", params={"cursor": cursor, "status": "ACTIVE"}).status_code == 422
    assert client.get("/v1/runs", params={"cursor": cursor[:-10] + "tampered"}).status_code == 422
    app.dependency_overrides[identity] = lambda: Identity(tenant, "different", True)
    assert client.get("/v1/runs", params={"cursor": cursor}).status_code == 422


def test_workspace_queries_are_batched_and_never_read_artifact_objects(client, tenant, make_run, monkeypatch):
    run_id = make_run()
    reference = objects.put(tenant, run_id, b"private artifact content")
    with db.transaction(tenant) as s:
        s.add(db.Artifact(tenant_id=tenant, run_id=run_id, name="report", kind="summary", ref=reference))
    def forbidden(*args):
        raise AssertionError("Workspace must never fetch full object contents")
    monkeypatch.setattr(objects, "get", forbidden)
    queries = []
    def count(*args):
        queries.append(1)
    event.listen(db.engine, "before_cursor_execute", count)
    try:
        first = client.get("/v1/workspace")
        baseline = len(queries)
        assert first.status_code == 200
        assert first.json()["artifacts"][0]["content"] == ""
        for _ in range(8):
            make_run()
        queries.clear()
        result = client.get("/v1/workspace")
        assert result.status_code == 200
        assert len(queries) <= baseline + 1
        assert len(queries) < 25
    finally:
        event.remove(db.engine, "before_cursor_execute", count)


def test_pagination_applies_permissions_in_database_before_limit(client, tenant, make_run):
    owned = make_run()
    for _ in range(4):
        foreign = make_run()
        with db.transaction(tenant) as s:
            db.get(s, db.Run, tenant, foreign).actor = "other"
    with db.transaction(tenant) as s:
        auth = db.get(s, db.Authorization, tenant, "tester")
        auth.data = {**auth.data, "project_permissions": {}}
    app.dependency_overrides[identity] = lambda: Identity(tenant, "tester", False)
    response = client.get("/v1/runs?limit=1").json()
    assert [row["id"] for row in response["items"]] == [owned]
    assert response["next_cursor"] is None


def test_revision_changes_on_persisted_run_and_config_updates(client, tenant, make_run):
    before = client.get("/v1/workspace/revision").json()["revision"]
    run_id = make_run()
    after = client.get("/v1/workspace/revision").json()["revision"]
    assert before != after
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        run.phase = "PLANNING"
        db.emit(s, run, "PLAN_UPDATED", "planning")
    assert client.get("/v1/workspace/revision").json()["revision"] != after


def test_catalog_is_bounded_and_omits_sensitive_payloads(client, tenant, make_run):
    for index in range(4):
        make_run(title=str(index))
    first = client.get("/v1/catalog/events?limit=2").json()
    assert len(first["items"]) == 2 and first["next_cursor"]
    assert all("payload" not in item for item in first["items"])
    assert client.get("/v1/catalog/events?limit=101").status_code == 422
    project = client.get("/v1/catalog/projects").json()["items"][0]
    assert "baseline" not in project and "protected_tests" not in project


def test_stream_closes_when_original_token_expires(client, tenant, make_run):
    run_id = make_run()
    app.dependency_overrides[identity] = lambda: Identity(tenant, "tester", True, int(time.time()) - 1)
    response = client.get(f"/v1/runs/{run_id}/events")
    assert "event: auth_expired" in response.text and "event: domain" not in response.text


async def test_sdk_reconnects_from_last_committed_id_and_deduplicates(monkeypatch):
    attempts = []
    def response(request):
        attempts.append(request.headers.get("Last-Event-ID"))
        if len(attempts) == 1:
            return httpx.Response(200, text='id: 1\nevent: domain\ndata: {"seq":1}\n\n')
        return httpx.Response(200, text='id: 1\ndata: {"seq":1}\n\nid: 2\ndata: {"seq":2}\n\nevent: end\ndata: {"terminal":true}\n\n')
    monkeypatch.setattr("forgeagent.sdk.asyncio.sleep", AsyncMock())
    async with Client(token=lambda: "fresh-token") as client:
        await client.http.aclose()
        client.http = httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(response))
        values = [value async for value in client.events("run")]
    assert values == [{"seq": 1}, {"seq": 2}] and attempts == ["0", "1"]


async def test_sdk_retains_structured_errors_and_never_retries_write():
    attempts = []
    def response(request):
        attempts.append(request.method)
        return httpx.Response(409, json={"code": "VERSION_CONFLICT", "message": "changed", "correlation_id": "request"})
    async with Client() as client:
        await client.http.aclose()
        client.http = httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(response))
        try:
            await client.request("POST", "/runs", {}, "stable-key")
        except ForgeError as error:
            assert error.payload["code"] == "VERSION_CONFLICT" and error.payload["correlation_id"] == "request"
    assert attempts == ["POST"]


def test_run_view_reports_unknown_environment_and_evidence_progress(client, make_run):
    run_id = make_run()
    result = client.get(f"/v1/runs/{run_id}").json()
    assert result["progress_kind"] == "indeterminate" and result["progress"] == 0
    assert result["checkpoint"]["environment_status"] == "unknown"
    assert not result["checkpoint"]["environment"]
    assert result["deadline"] > 0


@pytest.mark.parametrize("path", ["/v1/workspace", "/v1/workspace/revision", "/v1/catalog/events", "/metrics"])
def test_revoked_identity_cannot_read_console(client, tenant, path):
    with db.transaction(tenant) as s:
        db.get(s, db.Authorization, tenant, "tester").status = "revoked"
    assert client.get(path).status_code == 403


def test_event_history_cursor_uses_same_scope_as_catalog(client, tenant, make_run):
    run_id = make_run()
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        for index in range(105):
            db.emit(s, run, "NOTE", str(index))
    workspace = client.get("/v1/workspace", params={"run_id": run_id}).json()
    assert len(workspace["events"]) == 100 and workspace["event_next_cursor"]
    older = client.get("/v1/catalog/events", params={"run_id": run_id, "cursor": workspace["event_next_cursor"]}).json()
    assert older["items"] and not ({e["id"] for e in older["items"]} & {e["id"] for e in workspace["events"]})
    assert all("projection" not in e["detail"] for e in older["items"])


def test_action_ledger_does_not_query_per_action(client, tenant, make_run):
    run_id = make_run()
    with db.transaction(tenant) as s:
        for index in range(15):
            s.add(db.Action(tenant_id=tenant, run_id=run_id, logical_key=str(index), tool="repo.read",
                args={}, effect_class="read", effect_digest="read"))
    queries = []
    def count(*args):
        queries.append(1)
    event.listen(db.engine, "before_cursor_execute", count)
    try:
        response = client.get(f"/v1/runs/{run_id}/actions")
        assert response.status_code == 200 and len(response.json()) == 15
        assert len(queries) < 12
    finally:
        event.remove(db.engine, "before_cursor_execute", count)


def test_oidc_roles_are_an_array_and_expiry_is_retained(monkeypatch):
    from types import SimpleNamespace

    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from forgeagent import auth
    from starlette.requests import Request

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "oidc_jwks_url", "https://identity.example/jwks")
    monkeypatch.setattr(settings, "oidc_issuer", "https://identity.example")
    monkeypatch.setattr(auth, "keys", lambda url: SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=key.public_key())))
    claims = {"iss": settings.oidc_issuer, "aud": settings.oidc_audience, "sub": "actor", "tenant_id": "tenant", "exp": int(time.time()) + 60}
    def read(roles, **overrides):
        token = jwt.encode({**claims, "roles": roles, **overrides}, key, algorithm="RS256")
        return identity(Request({"type": "http", "headers": [(b"authorization", ("Bearer " + token).encode())]}))
    assert not read("prefix-forge-admin-suffix").admin
    assert read(["forge-admin"]).admin
    assert read([]).expires_at == claims["exp"]
    with pytest.raises(Exception, match="expired"):
        read([], exp=1)


def test_metrics_use_aggregates_instead_of_loading_records(client, make_run, monkeypatch):
    make_run()
    def forbidden(*args, **kwargs):
        raise AssertionError("Metrics must use SQL aggregation")
    monkeypatch.setattr(db, "rows", forbidden)
    result = client.get("/v1/metrics")
    assert result.status_code == 200
    assert result.json()["runs"] == 1 and result.json()["tokens"] == 0
