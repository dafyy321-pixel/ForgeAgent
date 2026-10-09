"""Export a persisted paired experiment, including honest cost/report limitations."""

import argparse
import asyncio
import json
import os
from pathlib import Path

from forgeagent.domain import digest
from forgeagent.sdk import Client


def render(value):
    lines = ["# ForgeAgent experiment report", "", f"Experiment: `{value['id']}`. Status: `{value['status']}`.",
        f"Dataset: `{value['dataset']}`, model: `{value['model']}`. Independent cases: {value['independent_cases']}; repeats: {value['repetitions']}.", "",
        "| Configuration | Disposition rate | Model cost USD | Full cost USD | Mean wall seconds |", "|---|---:|---:|---:|---:|"]
    for name, summary in value["summaries"].items():
        cost = summary.get("research", {}).get("full_cost_usd")
        lines.append(f"| {name} | {summary['correct_disposition_rate']:.3f} | {summary['cost']:.6f} | {cost if cost is not None else 'incomplete'} | {summary['latency_mean']:.3f} |")
    lines += ["", "Paired case-cluster bootstrap comparisons:", "", "```json", json.dumps(value["comparisons"], indent=2), "```", "",
        "Failure, recovery and missing-cost evidence:", "", "```json", json.dumps({name: summary.get("research", {"unavailable": "Legacy immutable report has no full-cost or recovery measurements"}) for name, summary in value["summaries"].items()}, indent=2), "```", "",
        value["limitations"], "", "Fixture results validate machinery only; they do not measure real-model coding quality or savings.",
        "Recovery latency starts at a recovered lease claim and ends at its first committed decision/tool/verification progress; pending recoveries remain censored.",
        "Public historical regression pilots are not blind held-out evidence. Official benchmark scores require the external harness.", "",
        f"Report digest: `{digest(value)}`."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("evaluation_id")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    async def main():
        async with Client(os.getenv("FORGE_API_URL", "http://127.0.0.1:8000"), os.getenv("FORGE_ACCESS_TOKEN")) as client:
            value = await client.request("GET", "/evaluations/" + args.evaluation_id)
        with args.output.open("x", encoding="utf-8") as output:
            output.write(render(value))
    asyncio.run(main())
