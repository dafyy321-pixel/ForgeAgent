"""Write a reviewed pilot plan only; never launch a paid experiment."""

import argparse
import json
from pathlib import Path

from forgeagent.pilot import prepare_plan

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("input", type=Path, help="JSONL prepared external cases from benchmark.py prepare")
parser.add_argument("output", type=Path)
parser.add_argument("--dataset-id", required=True)
parser.add_argument("--split", choices=["development", "held_out"], default="development")
parser.add_argument("--task-budget-usd", required=True)
parser.add_argument("--repetitions", type=int, default=3)
args = parser.parse_args()
values = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
result = prepare_plan(values, args.dataset_id, args.split, {"max_cost_usd": args.task_budget_usd}, args.repetitions)
args.output.parent.mkdir(parents=True, exist_ok=True)
with args.output.open("x", encoding="utf-8") as output:
    output.write(json.dumps(result, indent=2, ensure_ascii=False))
