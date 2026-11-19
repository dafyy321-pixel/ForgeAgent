"""Build authored, reproducible development repositories and a paired experiment.

Only fixed fixture sources below are executed during preparation. No provider or
database is used. Operator-owned reference changes stay outside the Git bundles.
"""

import argparse
import json
import re
from pathlib import Path

from forgeagent.coding_cases import run_acceptance
from forgeagent.domain import AcceptanceContract, BuildContract, Harness, digest
from forgeagent.evaluations import DatasetInput, ExperimentInput
from forgeagent.sandbox import git, sandbox
from forgeagent.workspace import import_commits


def repairs():
    inventory = """def stock_levels(events):
    levels = {}
    for sku, change in events:
        levels[sku] = change
    return levels
"""
    windows = """export function overlaps(a: [number, number], b: [number, number]): boolean {
  if (a[0] >= a[1] || b[0] >= b[1]) return false;
  return a[0] <= b[1] && b[0] <= a[1];
}
"""
    return [
        {"id": "fixture-stock-v1", "language": "python", "family": "inventory-aggregation",
         "goal": "Fix stock_levels so repeated changes accumulate per SKU, including negative adjustments. Preserve zero balances.",
         "baseline": {"src/__init__.py": "", "src/inventory.py": inventory},
         "fixed": {"src/inventory.py": inventory.replace("levels[sku] = change", "levels[sku] = levels.get(sku, 0) + change")},
         "argv": ["python", "-m", "unittest", "discover", "-s", "acceptance"],
         "protected_tests": {"acceptance/test_stock.py": """import unittest
from src.inventory import stock_levels

class StockLevels(unittest.TestCase):
    def test_multiple_skus(self):
        self.assertEqual(stock_levels([("a", 4), ("b", 7), ("a", -1), ("b", -7)]), {"a": 3, "b": 0})

    def test_empty(self):
        self.assertEqual(stock_levels([]), {})
"""}},
        {"id": "fixture-window-v1", "language": "typescript", "family": "half-open-intervals",
         "goal": "Fix overlaps for half-open reservation intervals. Adjacent and empty ranges never overlap; preserve symmetry.",
         "baseline": {"src/windows.ts": windows, "package.json": '{"private":true,"type":"module"}\n'},
         "fixed": {"src/windows.ts": windows.replace("<=", "<")},
         "argv": ["node", "--test", "acceptance/windows.test.mjs"],
         "protected_tests": {"acceptance/windows.test.mjs": """import test from 'node:test';
import assert from 'node:assert/strict';
import { overlaps } from '../src/windows.ts';
test('interval boundaries', () => {
  for (const [left, right, expected] of [
    [[1, 4], [4, 8], false], [[1, 5], [4, 8], true], [[1, 2], [3, 5], false],
    [[2, 2], [1, 4], false], [[1, 4], [2, 3], true],
  ]) {
    assert.equal(overlaps(left, right), expected);
    assert.equal(overlaps(right, left), expected);
  }
});
"""}},
    ]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prepare(destination, image):
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
        raise ValueError("Supply the inspected digest of the Python/Node sandbox image")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    case_rows, validation = [], []
    for case in repairs():
        folder = destination / case["id"]
        repo = folder / "repository"
        repo.mkdir(parents=True)
        git(repo, "init", "--quiet", "--initial-branch=main", "--object-format=sha1")
        commit, = import_commits(repo, [case["baseline"]])
        git(repo, "update-ref", "refs/heads/main", commit)
        git(repo, "read-tree", "-u", "-m", commit)
        reference = {**case["baseline"], **case["fixed"]}
        baseline_exit = run_acceptance(case, case["baseline"])["exit_code"]
        reference_exit = run_acceptance(case, reference)["exit_code"]
        patch, evidence = sandbox.delivery(case["baseline"], reference)
        if (baseline_exit, reference_exit, evidence["status"]) != (1, 0, "passed"):
            raise RuntimeError(f"Fixture validation failed for {case['id']}: baseline={baseline_exit}, reference={reference_exit}")
        validation.append({"id": case["id"], "commit": commit, "baseline_exit": baseline_exit,
                           "reference_exit": reference_exit, "reference_patch_digest": digest(patch.encode()),
                           "patch_application": evidence["status"], "environment": "local authored fixtures"})
        contract = AcceptanceContract(id=case["id"] + "@1", argv=case["argv"], protected_tests=case["protected_tests"])
        write_json(folder / "acceptance.json", {**contract.model_dump(), "build": BuildContract(dependency_image=image).model_dump()})
        source = {"repository": "synthetic/" + case["id"], "base_commit": commit, "language": case["language"]}
        rows = [{"id": case["id"], "task": {"goal": case["goal"], "allowed_paths": ["src"]},
                 "budget": {"max_cost_usd": "0.50"}, "family": case["family"]}]
        if case["language"] == "typescript":
            rows.append({"id": "clarify-window", "task": {"mode": "discuss", "allowed_paths": ["src"],
                         "goal": "Discuss adding automatic reservation expiry. The retention duration needs the owner's decision. Ask for it before drafting a plan."},
                         "budget": {"max_cost_usd": "0.50"}, "capabilities": ["repo.read"],
                         "expected_status": "PAUSED", "expected_reason": "等待你的回复", "family": "unspecified-retention"})
        else:
            rows.append({"id": "token-admission", "task": {"goal": "Inspect the stock event format within the assigned token budget.",
                         "allowed_paths": ["src"]}, "capabilities": ["repo.read"], "budget": {"max_cost_usd": "0.50", "max_tokens": 1},
                         "expected_status": "PAUSED", "expected_reason": "TOKEN_BUDGET_LIMIT", "family": "token-admission"})
        for row in rows:
            family = row.pop("family")
            row.update(project_id=case["id"], provenance={**source, "task_id": row["id"], "problem_family": family,
                                                          "source_digest": digest([commit, row, contract.model_dump()])})
            case_rows.append(row)
    dataset = DatasetInput(id="synthetic-profile-smoke@1", split="development", cases=case_rows,
                           source="Authored synthetic development fixtures for cross-profile workflow checks")
    harness = Harness(memory=False, delegation=False, planning=False, summarization=False, action_fusion=False)
    experiment = ExperimentInput(dataset_id=dataset.id, model="configured", seed=42, repetitions=1, max_total_cost_usd="4",
        configurations=[{"name": "baseline", "harness": harness},
                        {"name": "recovery", "harness": harness.model_copy(update={"evidence_recovery": True})}])
    write_json(destination / "dataset.json", dataset.model_dump(mode="json"))
    write_json(destination / "experiment.json", experiment.model_dump(mode="json"))
    write_json(destination / "validation.json", validation)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--image", required=True, help="Immutable digest of the reviewed Python/Node sandbox")
    args = parser.parse_args()
    prepare(args.destination, args.image)
