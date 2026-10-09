"""Small fixture-only machinery study; no paid provider call or quality claim."""

import argparse
import asyncio
import json
import subprocess
from pathlib import Path

from forgeagent import db, evaluations, service
from forgeagent.domain import uid
from forgeagent.worker import Worker
from research_report import render

parser = argparse.ArgumentParser()
parser.add_argument("output", type=Path)
args = parser.parse_args()
if args.output.exists() or args.output.with_suffix(".md").exists():
    parser.error("Choose a new output path; previous study evidence is immutable")


async def main():
    tenant = "local-study-" + uid()
    service.provision(tenant, "researcher")
    with db.transaction(tenant) as s:
        evaluations.register(s, tenant, evaluations.DatasetInput(id="mechanism@1", source="One deterministic Runtime Lab fixture; not an external or blind benchmark",
            cases=[{"id": "addition-fixture", "project_id": "runtime-lab", "task": {"goal": "Fix addition", "allowed_paths": ["src"]}, "budget": {"max_cost_usd": "0.10"}}]))
        result = evaluations.start(s, tenant, "researcher", evaluations.ExperimentInput(dataset_id="mechanism@1", model="fixture", repetitions=2,
            configurations=[{"name": "baseline", "harness": {"memory": False}}, {"name": "no_plan", "harness": {"memory": False, "planning": False}},
                {"name": "no_summary", "harness": {"memory": False, "summarization": False}}, {"name": "fusion", "harness": {"memory": False, "observation_fusion": True}}]))
        identity, ids = result["id"], result["runs"]
    for _ in range(12):
        for run_id in ids:
            await Worker(target_run=run_id).once(tenant)
        with db.transaction(tenant) as s:
            result = evaluations.report(s, db.get(s, db.Evaluation, tenant, identity), tenant)
            if result["status"] == "completed":
                break
    if result["status"] != "completed":
        raise RuntimeError("Local mechanism study did not complete")
    result["execution_provenance"] = {"source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True)),
        "evidence_kind": "local-fixture-mechanism-only"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2, ensure_ascii=False)
    with args.output.with_suffix(".md").open("x", encoding="utf-8") as output:
        output.write(render(result))
    print(f"Local fixture study: {result['successes']}/{result['total']}; {result['independent_cases']} independent case; no quality or savings inference")


asyncio.run(main())
