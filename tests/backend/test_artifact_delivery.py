from forgeagent import db
from forgeagent.domain import digest
from forgeagent.storage import objects
from forgeagent.worker import Worker


def assert_public_metadata(client, run_id, artifact_id):
    lists = [
        client.get(f"/v1/runs/{run_id}/artifacts").json(),
        client.get("/v1/catalog/artifacts", params={"run_id": run_id}).json()["items"],
        client.get("/v1/workspace", params={"run_id": run_id}).json()["artifacts"],
    ]
    response = client.get(f"/v1/artifacts/{artifact_id}/download")
    assert response.status_code == 200
    for rows in lists:
        row = next(row for row in rows if row["id"] == artifact_id)
        assert row["digest"] == digest(response.content)
        assert row.get("bytes", row.get("size")) in (len(response.content), f"{len(response.content)} bytes")
    return response


def test_legacy_private_report_publishes_the_digest_of_its_redacted_bytes(client, tenant, make_run):
    run_id = make_run()
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        run.state = {**run.state, "acceptance": {**run.state["acceptance"], "protected_tests": {"x": "secret"}}}
        ref = objects.put(tenant, run_id, {"result": {"output": "private result"}, "checks": []})
        artifact = db.Artifact(tenant_id=tenant, run_id=run_id, kind="test_report", ref=ref,
                               name="verification.json", verified=True, version=1)
        s.add(artifact)
        s.flush()
        artifact_id = artifact.id
    response = assert_public_metadata(client, run_id, artifact_id)
    assert b"private result" not in response.content
    assert objects.get(tenant, ref) != response.content


async def test_worker_materializes_public_report_before_delivery(client, tenant, make_run):
    with db.transaction(tenant) as s:
        project = db.get(s, db.Project, tenant, "runtime-lab")
        contract = {**project.data["acceptance"], "protected_tests": {"hidden/acceptance.py": "# private"}}
        project.data = {**project.data, "acceptance": contract}
    run_id = make_run()
    worker = Worker()
    for _ in range(12):
        await worker.once(tenant)
        with db.transaction(tenant) as s:
            reports = db.rows(s, db.Artifact, tenant, run_id=run_id, kind="test_report")
            if reports:
                report_id, ref = reports[0].id, reports[0].ref
                [evidence] = db.rows(s, db.VerificationResult, tenant, run_id=run_id)
                assert evidence.data["evidence_ref"]["digest"] != ref["digest"]
                break
    else:
        raise AssertionError("Worker never published a verification report")
    assert ref.get("public_report_version") == 1
    response = assert_public_metadata(client, run_id, report_id)
    assert response.json()["result"]["output"] == "Protected acceptance diagnostics are private"
    assert objects.get(tenant, ref) == response.content


def test_deep_link_reaches_the_selected_artifact_beyond_the_workspace_window(client, tenant, make_run):
    run_id = make_run()
    with db.transaction(tenant) as s:
        ref = objects.put(tenant, run_id, b"file")
        for number in range(201):
            s.add(db.Artifact(tenant_id=tenant, run_id=run_id, ref=ref, id=f"artifact-{number:03}",
                              kind="remote", name="file", verified=False, version=1))
    def page(**filters):
        return client.get("/v1/workspace", params={"run_id": run_id, **filters}).json()["artifacts"]
    first_page = {row["id"] for row in page()}
    old = ({f"artifact-{number:03}" for number in range(201)} - first_page).pop()
    focused = page(artifact_id=old)
    assert len(focused) == 200 and focused[0]["id"] == old
