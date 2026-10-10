"""Measure cold, warm and one-file updates; no model effectiveness claim."""

import argparse
import json
import time
from pathlib import Path

from forgeagent.code_index import SyntaxCache, retrieve

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
manifest = {f"pkg/{i:04}.py": f"def helper_{i}(): return {i}\n" + "# filler\n" * 500 for i in range(1200)}
manifest["z/invoice.py"] = "def quantize_invoice(): return 42\n"
cache, samples = SyntaxCache(), []
for stage in ["cold", "warm", "changed"]:
    if stage == "changed":
        manifest["z/invoice.py"] = "def quantize_invoice(): return 43\n"
    started = time.perf_counter()
    result = retrieve(manifest, "quantize invoice", "structure", cache=cache, cache_scope=("pilot", "run"))
    samples.append({"stage": stage, "seconds": time.perf_counter() - started,
                    "parsed_files": result["parsed_files"], "cache_hits": result["cache_hits"],
                    "indexed_files": result["indexed_files"], "indexed_bytes": result["indexed_bytes"],
                    "eligible_files": result["eligible_files"], "first_path": result["items"][0]["path"]})
report = {"schema_version": 1, "kind": "synthetic_index_mechanism", "files": len(manifest),
          "samples": samples, "cache_metadata_accounted_bytes": cache.bytes,
          "model_run": "not_run", "limitations": ["Three measurements, no statistical latency claim.",
          "Relevance selection covers a bounded syntax view, not a full repository graph.",
          "Metadata accounting is serialized bytes plus overhead allowance, not process RSS."]}
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
