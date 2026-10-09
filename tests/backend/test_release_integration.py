"""Opt-in release gates. These tests never replace unavailable infrastructure with mocks."""

import asyncio
import json
import os
from pathlib import Path

import pytest
from forgeagent import db, remote
from forgeagent.config import settings
from forgeagent.domain import Budget, uid
from forgeagent.sandbox import sandbox
from forgeagent.worker import Worker

pytestmark = pytest.mark.skipif(os.environ.get("FORGE_TEST_RELEASE") != "1", reason="Requires isolated real release infrastructure")


def fact(name, value):
    destination = Path(os.environ["FORGE_RELEASE_FACTS_DIR"])
    destination.mkdir(parents=True, exist_ok=True)
    with (destination / (name + ".json")).open("x", encoding="utf-8") as output:
        json.dump({"source_commit": os.environ["GITHUB_SHA"], **value}, output)


async def test_live_model_completes_with_settled_provider_usage(tenant, make_run):
    assert settings.model_api_key and settings.model_id and settings.model_provider in {"openai", "anthropic"}
    assert settings.input_price > 0 and settings.output_price > 0
    run_id = make_run(model="configured", budget=Budget(max_cost_usd=os.environ.get("FORGE_RELEASE_MODEL_BUDGET", "1.00"), max_turns=12),
        title="Release provider integration", task={"goal": "Read src/calculator.py, fix add to return left + right, then propose_completion for independent verification.", "allowed_paths": ["src"]})
    async with asyncio.timeout(300):
        for _ in range(60):
            await Worker(target_run=run_id).once(tenant)
            with db.transaction(tenant) as s:
                run = db.get(s, db.Run, tenant, run_id)
                if run.status == "SUCCEEDED":
                    calls = db.rows(s, db.ModelCall, tenant, run_id=run_id)
                    assert calls and all(call.data.get("receipt_state") == "applied" for call in calls)
                    entries = db.rows(s, db.BudgetEntry, tenant, account_id=run_id)
                    assert entries and all(entry.status == "settled" for entry in entries)
                    assert db.get(s, db.BudgetAccount, tenant, run_id).reserved == 0
                    fact("model", {"provider": settings.model_provider, "model_id": settings.model_id,
                        "profile": run.state["semantic"]["model_profile"], "call_count": len(calls),
                        "cost_micros": db.get(s, db.BudgetAccount, tenant, run_id).spent})
                    return
                assert run.status not in {"FAILED", "PAUSED", "CANCELLED"}, "Live provider run failed; inspect isolated task audit"
            await asyncio.sleep(.5)
    pytest.fail("Live provider integration did not finish")


async def test_live_gvisor_manager_enforces_isolation(tenant):
    assert settings.sandbox_manager_url and settings.sandbox_manager_secret, "Release requires the independent manager"
    root = sandbox.restore(tenant, uid(), 1, {"marker": "immutable"})
    code = """import os,socket
assert os.getuid()==10001
try:
 open('marker','w').write('bad')
except OSError: pass
else: raise AssertionError('workspace writable')
try:
 socket.create_connection(('1.1.1.1',443),timeout=1)
except OSError: pass
else: raise AssertionError('network reachable')
print('gvisor manager isolated')
"""
    image = await sandbox.image_digest()
    result = await sandbox.execute(root, ["python", "-c", code], image, readonly=True)
    assert result["exit_code"] == 0
    assert result["sandbox_profile"]["runtime"] == "runsc"
    assert (root / "marker").read_text() == "immutable"
    fact("sandbox", {"image_digest": image, "profile": result["sandbox_profile"]})


async def test_live_protocol_negotiation_and_isolated_probes():
    path = Path(os.environ["FORGE_RELEASE_CONNECTIONS_FILE"])
    values = json.loads(path.read_text(encoding="utf-8"))
    expected = {("mcp", "2025-11-25"), ("mcp", "2026-07-28"), ("a2a", "0.3.0"), ("a2a", "1.0")}
    assert {(value["connection"]["kind"], value["connection"]["protocol"]) for value in values} == expected
    from forgeagent.domain import digest

    bindings = []
    for value in values:
        assert value.get("isolated_test_service") is True, "Live probes must target isolated test services"
        connection, probe = value["connection"], value["probe"]
        negotiated = await remote.discover(connection)
        assert negotiated["protocol"] == connection["protocol"]
        result = await remote.rpc(connection, probe["method"], probe["params"])
        assert isinstance(result, dict) and not result.get("isError")
        for key in probe["required_result_fields"]:
            assert key in result
        bindings.append({"kind": connection["kind"], "version": connection["protocol"],
            "connection_digest": digest(connection), "negotiated_digest": digest(negotiated), "probe_digest": digest(probe)})
    fact("protocols", {"bindings": bindings})
