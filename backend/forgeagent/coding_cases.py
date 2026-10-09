"""Authored development cases. Reference patches are operator data, never model inputs."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .code_index import retrieve
from .domain import digest
from .sandbox import sandbox
from .tokenization import count
from .workspace import write_tree


def cases():
    python_source = '''from decimal import Decimal, ROUND_HALF_UP

def to_minor(value):
    amount = Decimal(str(value)) * 100
    return int(amount)
'''
    typescript_source = '''export type Price = string;
export const toMinor = (price: Price): number => {
  if (!/^-?\\d+(?:\\.\\d{1,2})?$/.test(price)) throw new Error('invalid price');
  const negative = price.startsWith('-');
  const [whole, fraction = ''] = price.replace(/^-/, '').split('.');
  return (negative ? -1 : 1) * (Number(whole) * 100 + Number(fraction));
};
'''
    return [
        {"id": "python-invoice-rounding", "runtime": "Python >=3.12 (stdlib only)",
         "goal": "Fix invoice_total: invoice line unit prices must round to minor units with decimal ROUND_HALF_UP, including negative adjustments; preserve per-unit rounding before quantity multiplication.",
         "baseline": {"src/__init__.py": "", "src/z_money.py": python_source,
             "src/invoice.py": "from .z_money import to_minor\n\ndef invoice_total(lines):\n    return sum(to_minor(price) * quantity for price, quantity in lines)\n",
             "src/a_invoice_guide.py": "# invoice_total documentation; preserve invoice_total interface\n",
             "tests/test_smoke.py": "import unittest\nfrom src.invoice import invoice_total\nclass Smoke(unittest.TestCase):\n    def test_empty(self): self.assertEqual(invoice_total([]), 0)\n"},
         "fixed": {"src/z_money.py": python_source.replace("int(amount)", "int(amount.to_integral_value(rounding=ROUND_HALF_UP))")},
         "targets": ["src/invoice.py", "src/z_money.py"],
         "argv": ["python", "-m", "unittest", "discover", "-s", "private_acceptance"],
         "protected_tests": {"private_acceptance/test_invoice.py": '''import unittest
from src.invoice import invoice_total
class Acceptance(unittest.TestCase):
    def test_half_cent(self): self.assertEqual(invoice_total([('1.005', 2)]), 202)
    def test_decimal(self): self.assertEqual(invoice_total([('2.675', 1)]), 268)
    def test_adjustment(self): self.assertEqual(invoice_total([('-0.005', 1)]), -1)
    def test_multiple(self): self.assertEqual(invoice_total([('2.00', 3), ('1.005', 1)]), 701)
    def test_empty(self): self.assertEqual(invoice_total([]), 0)
'''},
        },
        {"id": "typescript-invoice-fraction", "runtime": "Node >=24 native erasable TypeScript (no npm dependencies)",
         "goal": "Fix invoiceTotal: a decimal price with one fractional digit must be interpreted as tenths, not cents; keep two digit, whole, negative and rejected invalid price behavior.",
         "baseline": {"src/z_money.ts": typescript_source,
             "src/invoice.ts": "import { toMinor } from './z_money.ts';\nexport interface Line { price: string; quantity: number }\nexport const invoiceTotal = (lines: Line[]): number => lines.reduce((sum, line) => sum + toMinor(line.price) * line.quantity, 0);\n",
             "src/a_invoice_guide.ts": "// invoiceTotal documentation; preserve invoiceTotal interface\n",
             "package.json": '{"name":"controlled-invoice-case","private":true,"type":"module","engines":{"node":">=24"}}\n',
             "tests/smoke.mjs": "import assert from 'node:assert/strict';\nimport { invoiceTotal } from '../src/invoice.ts';\nassert.equal(invoiceTotal([]), 0);\n"},
         "fixed": {"src/z_money.ts": typescript_source.replace("Number(fraction)", "Number(fraction.padEnd(2, '0'))")},
         "targets": ["src/invoice.ts", "src/z_money.ts"],
         "argv": ["node", "--test", "private_acceptance/invoice.test.mjs"],
         "protected_tests": {"private_acceptance/invoice.test.mjs": '''import test from 'node:test';
import assert from 'node:assert/strict';
import { invoiceTotal } from '../src/invoice.ts';
test('one fractional digit and quantity', () => assert.equal(invoiceTotal([{price:'2.5',quantity:2}]), 500));
test('whole and hundredths', () => assert.equal(invoiceTotal([{price:'2',quantity:1},{price:'2.05',quantity:1}]), 405));
test('negative adjustment', () => assert.equal(invoiceTotal([{price:'-0.5',quantity:1}]), -50));
test('empty', () => assert.equal(invoiceTotal([]), 0));
test('invalid precision stays rejected', () => assert.throws(() => invoiceTotal([{price:'1.234',quantity:1}])));
'''},
        },
    ]


def run_acceptance(case, content):
    # This executes only these authored sources, never an arbitrary agent patch.
    with tempfile.TemporaryDirectory(prefix="forge-controlled-case-") as temporary:
        root = Path(temporary)
        write_tree(root, {**content, **case["protected_tests"]})
        argv = [sys.executable, *case["argv"][1:]] if case["argv"][0] == "python" else case["argv"]
        result = subprocess.run(argv, cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30)
        return {"exit_code": result.returncode, "output": (result.stdout + result.stderr)[-16000:]}


def prepare(destination):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    results, dataset = [], []
    for case in cases():
        directory = destination / case["id"]
        directory.mkdir()
        repo = directory / "repository"
        repo.mkdir()
        write_tree(repo, case["baseline"])
        env = {**os.environ, "GIT_AUTHOR_DATE": "2026-10-09T00:00:00+00:00", "GIT_COMMITTER_DATE": "2026-10-09T00:00:00+00:00",
               "GIT_AUTHOR_NAME": "Forge controlled cases", "GIT_COMMITTER_NAME": "Forge controlled cases",
               "GIT_AUTHOR_EMAIL": "case@example.invalid", "GIT_COMMITTER_EMAIL": "case@example.invalid"}
        def git(*args):
            return subprocess.check_output(["git", "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false", *args], cwd=repo, env=env).decode().strip()
        git("init", "--quiet", "--initial-branch=main")
        git("add", "--all")
        git("commit", "--quiet", "-m", "controlled defect baseline")
        commit = git("rev-parse", "HEAD")
        git("bundle", "create", str(directory / "repository.bundle"), "HEAD")
        after = {**case["baseline"], **case["fixed"]}
        patch = sandbox.patch(case["baseline"], after)
        application = sandbox.check_patch(case["baseline"], after, patch)
        baseline_result = run_acceptance(case, case["baseline"])
        reference_result = run_acceptance(case, after)
        if baseline_result["exit_code"] != 1 or reference_result["exit_code"] != 0 or application["status"] != "passed":
            raise RuntimeError(f"Controlled case {case['id']} did not demonstrate baseline failure/reference pass: {baseline_result}, {reference_result}")
        (directory / "reference.patch").write_text(patch, encoding="utf-8", newline="")
        contract = {"id": case["id"] + "@1", "kind": "command", "argv": case["argv"], "timeout": 30,
                    "protected_tests": case["protected_tests"], "protected_paths": ["tests/*", "package.json"]}
        (directory / "acceptance.json").write_text(json.dumps(contract, indent=2), encoding="utf-8")
        retrieval = {}
        for policy in ("lexical", "structure"):
            value = retrieve(case["baseline"], case["goal"], policy, limit=2)
            selected = [i["path"] for i in value["items"]]
            retrieval[policy] = {"paths": selected, "target_recall_at_2": len(set(selected) & set(case["targets"])) / len(case["targets"]),
                                 "local_tokens": count(value["items"])}
        result = {"id": case["id"], "commit": commit, "baseline_digest": digest(case["baseline"]),
                  "patch_digest": digest(patch.encode()), "runtime": case["runtime"],
                  "baseline": baseline_result, "reference": reference_result, "patch_application": application, "retrieval": retrieval,
                  "model_run": "not_run", "sandbox_run": "not_run", "case_origin": "authored development case"}
        results.append(result)
        dataset.append({"id": case["id"], "project_id": case["id"], "task": {"goal": case["goal"], "allowed_paths": ["src"]},
                        "budget": {"max_cost_usd": "0.50", "max_turns": 20}})
    report = {"schema_version": 1, "cases": results,
              "limitations": "Two authored local development cases; reference patches are human-authored. No real model, production sandbox, blind holdout or generalization claim."}
    (destination / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (destination / "dataset.json").write_text(json.dumps({"id": "controlled-coding@1", "split": "development",
        "source": report["limitations"], "cases": dataset}, indent=2), encoding="utf-8")
    (destination / "experiment.json").write_text(json.dumps({"dataset_id": "controlled-coding@1", "model": "configured",
        "configurations": [{"name": p, "harness": {"code_retrieval": p, "memory": False, "delegation": False}} for p in ("lexical", "structure")],
        "repetitions": 3, "seed": 42, "max_total_cost_usd": "6"}, indent=2), encoding="utf-8")
    return report
