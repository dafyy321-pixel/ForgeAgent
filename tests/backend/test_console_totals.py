from forgeagent import db
from forgeagent.api import app
from forgeagent.auth import Identity, identity


def test_totals_and_filtered_pages_include_history_outside_workspace_page(client, tenant, make_run):
    oldest = make_run(title="old blocker")
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, oldest)
        run.status = "PAUSED"
        db.get(s, db.BudgetAccount, tenant, oldest).spent = 1234
        s.add(db.Artifact(tenant_id=tenant, run_id=oldest, name="old.patch", kind="patch",
                          ref={"digest": "a" * 64, "bytes": 0}, verified=True))
    for _ in range(3):
        make_run()
    page = client.get("/v1/workspace?limit=2").json()
    assert len(page["runs"]) == 2 and oldest not in [r["id"] for r in page["runs"]]
    summary = client.get("/v1/workspace/summary").json()
    assert summary["total"] == 4 and summary["attention"] == 1 and summary["cost_usd"] == 0.001234
    assert summary["attention_runs"][0]["id"] == oldest
    assert client.get("/v1/runs?status=recovery&limit=1").json()["items"][0]["id"] == oldest
    artifacts = client.get("/v1/catalog/artifacts?verified=true&project=runtime-lab&limit=1").json()
    assert artifacts["items"][0]["run_id"] == oldest
    assert client.get("/v1/workspace/summary?project=absent").json()["total"] == 0
    with db.transaction(tenant) as s:
        db.get(s, db.Run, tenant, oldest).actor = "another"
        auth = db.get(s, db.Authorization, tenant, "tester")
        auth.data = {**auth.data, "project_permissions": {}}
    app.dependency_overrides[identity] = lambda: Identity(tenant, "tester", False)
    summary = client.get("/v1/workspace/summary").json()
    assert summary["total"] == 3 and summary["attention"] == 0 and summary["cost_usd"] == 0
    assert not client.get("/v1/catalog/artifacts").json()["items"]


def test_skill_counts_are_successful_visible_reads_and_catalog_cursor_is_filter_bound(client, tenant, make_run):
    run_id = make_run()
    with db.transaction(tenant) as s:
        for index in range(3):
            s.add(db.SkillVersion(tenant_id=tenant, id=f"counted-{index}", status="candidate",
                                 data={"name": "counted", "version": str(index), "category": "开发"}))
        for index, status in enumerate(["SUCCEEDED", "FAILED"]):
            s.add(db.Action(tenant_id=tenant, run_id=run_id, logical_key=f"counted:{index}", tool="skill.read",
                            effect_class="read", effect_digest="a" * 64, status=status, args={"skill_id": "counted-0"}))
    page = client.get("/v1/catalog/skills?limit=1").json()
    assert page["next_cursor"]
    cursor = page["next_cursor"]
    assert client.get("/v1/catalog/skills", params={"cursor": cursor, "status": "active"}).status_code == 422
    skills = client.get("/v1/catalog/skills?limit=100").json()["items"]
    assert next(row for row in skills if row["id"] == "counted-0")["calls"] == 1
