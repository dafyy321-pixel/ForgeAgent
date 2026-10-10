"""Prepare audited local repositories and import/export official benchmark records."""

import argparse
import json
from pathlib import Path

from forgeagent.benchmarks import export_predictions, normalize_external, normalize_swebench, prepare

parser = argparse.ArgumentParser()
parser.add_argument("mode", choices=["import-swebench", "import-external", "prepare", "export-predictions"])
parser.add_argument("input", type=Path)
parser.add_argument("output", type=Path)
parser.add_argument("--split", choices=["development", "held_out"], default="development")
parser.add_argument("--repository", type=Path)
parser.add_argument("--environment", type=Path, help="Reviewed JSON: image digest, argv, allowed_paths, acceptance_paths")
args = parser.parse_args()
if args.output.exists():
    parser.error("Output already exists; choose a new versioned path")
values = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
if args.mode == "export-predictions":
    result = export_predictions(values)
elif args.mode == "import-swebench":
    result = "\n".join(json.dumps(normalize_swebench(row, args.split), ensure_ascii=False) for row in values) + "\n"
elif args.mode == "import-external":
    result = "\n".join(json.dumps(normalize_external(row, args.split), ensure_ascii=False) for row in values) + "\n"
else:
    if not args.repository or not args.environment:
        parser.error("Preparation requires a local repository and reviewed environment configuration")
    environment = json.loads(args.environment.read_text(encoding="utf-8"))
    result = "\n".join(json.dumps(prepare(args.repository, row, environment["image"], environment["argv"],
        environment["allowed_paths"], environment.get("acceptance_paths")), ensure_ascii=False) for row in values) + "\n"
args.output.parent.mkdir(parents=True, exist_ok=True)
with args.output.open("x", encoding="utf-8") as output:
    output.write(result)
