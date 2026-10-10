"""Offline, reviewed benchmark import. Never execute downloaded code on the host."""

import json
import re
import tempfile
from pathlib import Path

from .domain import BuildContract, Fault, digest
from .repository import bundle_from_directory, commit_files
from .sandbox import git, raw_attributes
from .workspace import body, write_tree


def normalize_swebench(record, split):
    required = {"instance_id", "repo", "base_commit", "problem_statement", "test_patch"}
    if not required <= record.keys() or split not in {"development", "held_out"}:
        raise Fault("BENCHMARK_INPUT", "SWE-bench record or split is incomplete", 422)
    if not re.fullmatch(r"[0-9a-f]{40}([0-9a-f]{24})?", record["base_commit"]):
        raise Fault("BENCHMARK_INPUT", "Benchmark base commit must be a full immutable Git ID", 422)
    if not record["test_patch"].strip():
        raise Fault("BENCHMARK_ACCEPTANCE", "An independent acceptance patch is required", 422)
    # Gold solution patch is deliberately excluded from the review/task manifest.
    return {"schema": 1, "benchmark": "swe-bench", "instance_id": record["instance_id"],
        "repo": record["repo"], "base_commit": record["base_commit"], "split": split,
        "goal": record["problem_statement"], "acceptance_patch": record["test_patch"],
        "source_digest": digest(record), "provenance": "Imported record; license and environment require review"}


def normalize_external(record, split):
    """Reviewed Python/TS issue records; retain acceptance, exclude gold solution."""
    from .evaluations import CaseSource

    result = normalize_swebench(record, split)
    source = CaseSource(repository=record["repo"], base_commit=record["base_commit"],
        task_id=record["instance_id"], problem_family=record["problem_family"],
        language=record["language"], source_digest=digest(record))
    return {**result, "benchmark": "external-reviewed", "case_source": source.model_dump()}


def prepare(directory, case, environment_image, verification_argv, allowed_paths, acceptance_paths=None):
    from .api import ProjectInput

    if not environment_image.startswith("sha256:") or not verification_argv or not allowed_paths:
        raise Fault("BENCHMARK_ENVIRONMENT", "Pin a reviewed image, argv and modification scope", 422)
    source = Path(directory).resolve()
    if not re.fullmatch(r"[0-9a-f]{40}([0-9a-f]{24})?", case["base_commit"]):
        raise Fault("BENCHMARK_INPUT", "Review a full immutable base commit before preparation", 422)
    repository = bundle_from_directory(source, case["base_commit"])
    baseline = commit_files(source, repository.commit)
    protected = dict(case.get("protected_tests", {}))
    patch = case.get("acceptance_patch")
    if patch:
        if not acceptance_paths:
            raise Fault("BENCHMARK_ACCEPTANCE", "Review allowed acceptance paths before materializing a test patch", 422)
        from .repo_tools import execute

        with tempfile.TemporaryDirectory(prefix="forge-benchmark-") as directory:
            stage = Path(directory)
            git(stage, "init", "--quiet", "--object-format=" + ("sha256" if len(repository.commit) == 64 else "sha1"))
            write_tree(stage, baseline)
            raw_attributes(stage, baseline)
            _, after = execute(stage, "repo.apply_patch", {"patch": patch, "expected_workspace_digest": digest(baseline)},
                {"task": {"allowed_paths": acceptance_paths}, "acceptance": {}, "build": {}})
            for name in set(baseline) | set(after):
                if baseline.get(name) != after.get(name):
                    if name not in after:
                        raise Fault("BENCHMARK_ACCEPTANCE", "Acceptance patches may not delete files", 422)
                    protected[name] = body(after[name]).decode("utf-8")
    if not protected:
        raise Fault("BENCHMARK_ACCEPTANCE", "Provide independent acceptance test files", 422)
    identity = "benchmark-" + digest([case["benchmark"], case["instance_id"], repository.commit])[7:31]
    project = ProjectInput(id=identity, name=case["instance_id"], repository=repository,
        acceptance_id=identity + "@1", verification_argv=verification_argv, protected_tests=protected,
        build=BuildContract(dependency_image=environment_image))
    # Image is pinned in the reviewed execution environment, never supplied as a mutable tag.
    return {"project": project.model_dump(mode="json"), "environment_image": environment_image,
        "case": {"id": identity, "project_id": identity, "task": {"goal": case["goal"], "allowed_paths": allowed_paths},
                 **({"provenance": case["case_source"]} if case.get("case_source") else {})},
        "provenance": {key: case.get(key) for key in ["benchmark", "instance_id", "source_digest", "split", "base_commit", "repo"]},
        "input_digest": digest(case), "limitations": "Imports are bounded by ForgeAgent Git bundle/workspace limits; official benchmark scoring runs separately."}


def export_predictions(results):
    """SWE-bench harness JSONL; only delivered patches, never gold solutions."""
    rows, seen = [], set()
    for value in results:
        identity = value["instance_id"]
        if identity in seen or not isinstance(value["model_patch"], str) or not value.get("model_name_or_path"):
            raise Fault("BENCHMARK_PREDICTION", "Predictions need unique IDs, model labels and patch text", 422)
        seen.add(identity)
        rows.append(json.dumps({key: value[key] for key in ["instance_id", "model_name_or_path", "model_patch"]}, ensure_ascii=False))
    return "\n".join(rows) + ("\n" if rows else "")
