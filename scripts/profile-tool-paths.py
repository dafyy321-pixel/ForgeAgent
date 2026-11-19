"""Offline native-request and symbol-location comparisons on authored fixtures."""

import argparse
import json
import math
import tempfile
from pathlib import Path
from time import perf_counter_ns

from forgeagent import model_protocol, remote_contracts, repo_tools
from forgeagent.config import settings
from forgeagent.context import compile_context
from forgeagent.domain import canonical, digest
from forgeagent.tokenization import count


def size(value):
    return {"canonical_json_bytes": len(canonical(value)), "local_bpe_tokens": count(value)}


def tool_requests(number):
    target = f"lookup_{number - 1:03}"
    state = {"task": {"mode": "discuss", "goal": f"Use {target} to find the fixture record", "allowed_paths": ["src"]},
             "semantic": {"model_id": "offline-profile", "harness": {"tool_form": "native"}},
             "capabilities": ["repo.read", "external.read"], "remote_connections": [{"id": "docs", "data": {"tools": [
                {"operation": f"lookup_{i:03}", "effect": "read", "description": f"Read fixture index {i}.",
                 "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}
                for i in range(number)]}}]}

    def measure(observations):
        messages, _ = compile_context(state["task"], state, observations, [], [], 32768, 1024)
        request = model_protocol.build_request(messages, state, remote_contracts.catalog(state))
        return size(request["payload"]), set(request["names"].values())

    eager, catalog = measure([])
    target_name = remote_contracts.tool_name("docs", target)
    assert target_name in catalog
    state["semantic"]["harness"]["defer_remote_tools"] = True
    first, catalog = measure([])
    assert target_name not in catalog
    hits = remote_contracts.discover(state, target)
    assert [hit["name"] for hit in hits] == [target_name]
    state["turn"] = 1
    state["discovered_remote_tools"] = [target_name]
    second, catalog = measure([{"tool": "tools.discover", "status": "SUCCEEDED", "summary": hits}])
    assert target_name in catalog
    return {"remote_tools": number, "query": target, "eager_one_request": eager, "deferred_first_request": first,
            "deferred_after_exact_discovery": second, "deferred_two_requests": {k: first[k] + second[k] for k in first}}


def locate(root, count_files, repeats):
    for i in range(count_files):
        prefix = "target" if i == count_files - 1 else f"unrelated_{i}"
        (root / f"mod{i:02}.py").write_bytes("".join(
            f"def {prefix}_{j}():\n    return {j}\n\n" for j in range(3)).encode())

    def search(composed):
        receipts = []
        stages = 0
        def run(tool, args):
            receipt, _ = repo_tools.execute(root, tool, args, {})
            receipts.append(receipt)
            return receipt

        if count_files > 1:
            matches = run("repo.search", {"query": "def target_", "max_matches": 3})["matches"]
            path, = {match["path"] for match in matches}
            stages += 1
        else:
            path = "mod00.py"
        if composed:
            steps = []
            receipt = repo_tools.inspect_symbol(root, {"query": "target_", "path": path,
                "max_matches": 3, "max_lines": 10}, {}, steps.extend)
            receipts.append(receipt)
            excerpts = [item["content"] for item in receipt["excerpts"]]
            primitives = stages + 1 + len(steps)
            stages += 1
        else:
            symbols = run("repo.symbols", {"query": "target_", "path": path})["symbols"]
            excerpts = []
            for symbol in symbols:
                row = run("repo.read", {"path": path, "line_start": symbol["line"] - 1,
                    "max_lines": symbol["end_line"] - symbol["line"] + 1})
                excerpts.append(row["content"])
            primitives = len(receipts)
            # Action fusion can send all known read ranges in a single decision.
            stages += 2
        assert len(excerpts) == 3
        return receipts, excerpts, primitives, stages

    samples = {False: [], True: []}
    rows = {}
    for trial in range(repeats + 1):
        for composed in (bool(trial % 2), not bool(trial % 2)):
            start = perf_counter_ns()
            receipts, excerpts, primitives, stages = search(composed)
            elapsed = (perf_counter_ns() - start) / 1e6
            if trial:
                samples[composed].append(elapsed)
            rows[composed] = {"dependency_stages": stages, "primitive_calls": primitives,
                              "raw_receipts": size(receipts), "located_digest": digest(excerpts)}
    assert rows[False]["located_digest"] == rows[True]["located_digest"]
    for composed in rows:
        rows[composed]["local_tool_path_p95_ms"] = round(sorted(samples[composed])[math.ceil(.95 * repeats) - 1], 3)
    return {"files": count_files, "matched_symbols": 3, "fused_range_reads": rows[False], "inspect_symbol": rows[True]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=25)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("repeats must be positive")
    settings.model_provider, settings.model_protocol = "openai", "responses"
    settings.model_tokenizer, settings.max_output = "o200k_base", 1024
    settings.model_reasoning_effort, settings.model_stream, settings.model_supports_tools = None, False, True
    locations = []
    for n in (1, 32):
        with tempfile.TemporaryDirectory(prefix="forge-tool-profile-") as tmp:
            locations.append(locate(Path(tmp), n, args.repeats))
    result = {"kind": "authored-offline-comparison", "tokenizer": settings.model_tokenizer, "repeats": args.repeats,
              "native_requests": [tool_requests(n) for n in (2, 64)], "symbol_location": locations,
              "provider_input_tokens": None, "provider_bill_usd": None, "model_selection_accuracy": None,
              "end_to_end_verification": None,
              "limits": "Exact operation query and authored location plan. Canonical JSON bytes and local BPE estimates exclude HTTP framing, SDK continuation and provider billing. P95 measures only local tool execution on a warm filesystem. Multi-file paths include a common repository search."}
    report = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(report + "\n", encoding="utf8")
    print(report)


if __name__ == "__main__":
    main()
