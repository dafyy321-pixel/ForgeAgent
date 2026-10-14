"""Opt-in real model + REA + offline Docker recovery; no simulated provider or verifier."""

import asyncio
import hashlib
import json
import os
from pathlib import Path

import pytest
from forgeagent import db, rea, service
from forgeagent.config import settings
from forgeagent.domain import Budget, CreateRun, Harness, Task, uid
from forgeagent.worker import Worker

pytestmark = pytest.mark.skipif(os.environ.get("FORGE_TEST_LIVE_RECOVERY") != "1",
                               reason="Requires a billable model, pinned REA runtime and Docker sandbox")


async def test_live_offline_search_recovers_with_rea_and_protected_acceptance(tenant, client, tmp_path, monkeypatch):
    assert os.environ.get("FORGE_TEST_DOCKER") == "1"
    assert settings.model_provider == "openai" and settings.model_reasoning_effort == "high"
    runtime = Path(__file__).resolve().parents[2] / ".forge/rea"
    assert (runtime / "node_modules/rea-agents/scripts/rea.mjs").is_file()
    monkeypatch.setattr(settings, "rea_runtime_dir", runtime)
    monkeypatch.setattr(settings, "rea_target_roots", json.dumps([str(tmp_path)]))
    target = tmp_path / "search-reference"
    target.mkdir()
    (target / "package.json").write_text('{"name":"offline-search-reference","main":"index.js"}', encoding="utf8")
    (target / "index.js").write_text('''
function normalize(text) { return text.normalize('NFKC').toLowerCase().trim(); }
function search(items, query) {
  const q = normalize(query);
  return q ? items.filter(item => normalize(item).includes(q)) : [];
}
module.exports = { search };
''', encoding="utf8")
    protected = '''import unittest
from src.search import search
class SearchAcceptance(unittest.TestCase):
    def test_offline_search(self):
        items = ['Offline Search', '本地离线搜索', 'Ｆｏｒｇｅ', 'Other SEARCH']
        self.assertEqual(search(items, ' search '), [items[0], items[3]])
        self.assertEqual(search(items, '离线'), [items[1]])
        self.assertEqual(search(items, 'forge'), [items[2]])
        self.assertEqual(search(items, 'missing'), [])
        self.assertEqual(search(items, '  '), [])
        self.assertEqual(items, ['Offline Search', '本地离线搜索', 'Ｆｏｒｇｅ', 'Other SEARCH'])
'''
    project_id = "live-search-" + uid()
    registered = client.post("/v1/projects", json={"id": project_id, "name": "Live offline search recovery",
        "baseline": {"src/search.py": "def search(items, query):\n    return []\n",
                     "tests/test_search.py": "import unittest\nfrom src.search import search\n"
                        "class PublicSearch(unittest.TestCase):\n"
                        "    def test_query(self):\n"
                        "        self.assertEqual(search(['Offline Search', 'other'], ' search '), ['Offline Search'])\n"
                        "    def test_empty(self):\n"
                        "        self.assertEqual(search(['Offline Search'], '  '), [])\n"},
        "acceptance_id": "offline-search@1",
        "verification_argv": ["python", "-m", "unittest", "discover", "-s", "hidden_tests"],
        "protected_tests": {"hidden_tests/test_search.py": protected}})
    assert registered.status_code == 201, registered.text
    with db.transaction(tenant) as s:
        reference = rea.register(s, tenant, rea.RegisterTarget(project_id=project_id, label="Offline search reference", path=str(target)))
        run = service.create_run(s, tenant, "tester", CreateRun(project_id=project_id, model="configured",
            title="Live evidence-driven offline search recovery", harness=Harness(evidence_recovery=True),
            reference_targets=[reference["id"]], capabilities=["repo.read", "workspace.write", "tests.run", "reference.read"],
            budget=Budget(max_cost_usd="0.25", max_turns=16, max_wall_seconds=600),
            task=Task(goal=f"First call rea.analyze on reference target {reference['id']} to inspect the JavaScript search reference. "
                "A successful analysis receipt completes that step: do not repeat analysis on the unchanged reference. "
                "Then read src/search.py once and use its returned digest to write the implementation. "
                "Implement equivalent offline search(items, query) in Python: NFKC Unicode normalization, "
                "case-insensitive substring matching, strip query whitespace, empty query returns [], preserve item order and input list. "
                "Public tests are in tests/ and use python -m unittest discover -s tests (standard library runner). "
                "Only edit src/search.py, use standard library only, then propose_completion for independent verification.",
                allowed_paths=["src"])), uid(), admin=True)
        run_id = run.id
        # Seed a premature completion to exercise a real failing protected command
        # before any model call. Subsequent observations and repairs are live.
        run.state = {**run.state, "completion": "Verify unchanged baseline before repair"}
    await Worker(target_run=run_id).once(tenant)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        assert run.phase == "DIAGNOSING" and run.state["verification"]["verdict"] == "FAIL"
    async with asyncio.timeout(480):
        for _ in range(140):
            await Worker(target_run=run_id).once(tenant)
            with db.transaction(tenant) as s:
                run = db.get(s, db.Run, tenant, run_id)
                if run.status == "SUCCEEDED":
                    diagnoses = db.rows(s, db.Diagnosis, tenant, run_id=run_id)
                    attempts = db.rows(s, db.RecoveryAttempt, tenant, run_id=run_id)
                    calls = db.rows(s, db.ModelCall, tenant, run_id=run_id)
                    actions = db.rows(s, db.Action, tenant, run_id=run_id)
                    events = db.rows(s, db.Event, tenant, run_id=run_id)
                    account = db.get(s, db.BudgetAccount, tenant, run_id)
                    assert diagnoses and diagnoses[0].data["root_class"] == "unknown"
                    assert any(a.status == "SUCCEEDED" for a in attempts)
                    assert all(a.status in {"SUCCEEDED", "FAILED", "APPLIED"} for a in attempts)
                    assert any(a.tool == "rea.analyze" and a.status == "SUCCEEDED" for a in actions)
                    assert any(e.type == "RECOVERY_PROBE_COMPLETED" for e in events)
                    assert calls and all(c.data.get("receipt_state") == "applied" for c in calls)
                    assert account.reserved == 0
                    assert all(e.status == "settled" for e in db.rows(s, db.BudgetEntry, tenant, account_id=run_id))
                    reports = db.rows(s, db.VerificationResult, tenant, run_id=run_id)
                    assert {r.data["verdict"] for r in reports} == {"FAIL", "PASS"}
                    evidence = {"source_commit": os.environ["GITHUB_SHA"], "run_id": run_id,
                        "source_state": "working-tree-with-file-digests",
                        "source_files": {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in [
                            "backend/forgeagent/model_protocol.py", "backend/forgeagent/diagnosis.py",
                            "backend/forgeagent/processes.py", "tests/backend/test_live_recovery.py"]},
                        "profile": run.state["semantic"]["model_profile"], "calls": len(calls),
                        "cost_micros": account.spent, "reserved_micros": account.reserved,
                        "diagnoses": [d.data for d in diagnoses], "attempts": [a.status for a in attempts],
                        "actions": [{"tool": a.tool, "status": a.status} for a in actions],
                        "verdicts": [r.data["verdict"] for r in reports], "image_digest": run.state["image_digest"],
                        "limitations": "Seeded baseline failure; one real model task; static REA; local Docker runc, not production gVisor."}
                    destination = Path(os.environ["FORGE_RELEASE_FACTS_DIR"])
                    destination.mkdir(parents=True, exist_ok=True)
                    (destination / "live-recovery.json").write_text(json.dumps(evidence, indent=2), encoding="utf8")
                    return
                assert run.status not in {"FAILED", "PAUSED", "CANCELLED"}, f"{run.status}: {run.state.get('reason')} (run {run_id})"
            await asyncio.sleep(.5)
    pytest.fail(f"Live recovery did not finish; inspect run {run_id}")
