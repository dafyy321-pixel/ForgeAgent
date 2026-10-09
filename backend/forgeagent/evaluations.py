"""Versioned cases and paired experiments using the same durable runtime and verifier."""

import math
import random
from collections import defaultdict
from decimal import Decimal
from statistics import mean
from typing import Literal

from pydantic import Field, model_validator

from . import db, service
from .config import settings
from .domain import TERMINAL, Budget, CreateRun, Fault, Harness, Strict, Task, digest


class Case(Strict):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,100}$")
    project_id: str
    task: Task
    budget: Budget = Field(default_factory=Budget)
    capabilities: list[str] = Field(default_factory=lambda: ["repo.read", "workspace.write", "tests.run"])
    expected_status: Literal["SUCCEEDED", "PAUSED", "FAILED"] = "SUCCEEDED"
    expected_reason: str = ""

    @model_validator(mode="after")
    def expectation(self):
        if self.expected_status != "SUCCEEDED" and not self.expected_reason:
            raise ValueError("Non-success cases require a predefined reason code")
        if "external.write" in self.capabilities:
            raise ValueError("Evaluation cases may not perform external writes")
        return self


class DatasetInput(Strict):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+@[a-zA-Z0-9_.-]+$")
    split: Literal["development", "held_out"] = "development"
    cases: list[Case] = Field(min_length=1, max_length=120)
    source: str = Field(min_length=1, max_length=2000)


class Configuration(Strict):
    name: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,60}$")
    harness: Harness = Field(default_factory=Harness)
    skills: list[str] = Field(default_factory=list, max_length=20)


class ExperimentInput(Strict):
    dataset_id: str
    model: Literal["fixture", "configured"] = "configured"
    configurations: list[Configuration] = Field(min_length=2, max_length=6)
    repetitions: int = Field(3, ge=1, le=10)
    seed: int = 42
    max_total_cost_usd: Decimal = Field(Decimal("10"), gt=0, le=10000)
    noninferiority_margin: float = Field(0.02, ge=0, le=0.1)
    max_cost_ratio: float = Field(1, gt=0, le=2)


def register(s, tenant, body):
    if s.get(db.EvaluationDataset, (tenant, body.id)):
        raise Fault("IMMUTABLE_DATASET", "Register a new dataset version")
    if len({c.id for c in body.cases}) != len(body.cases):
        raise Fault("DUPLICATE_CASE", "Case identifiers must be unique", 422)
    bindings = {}
    for case in body.cases:
        project = db.get(s, db.Project, tenant, case.project_id)
        bindings[case.project_id] = digest(project.data)
    data = {**body.model_dump(mode="json"), "project_bindings": bindings}
    fingerprints = [digest({"project": bindings[c.project_id], "task": c.task.model_dump()}) for c in body.cases]
    for existing in db.rows(s, db.EvaluationDataset, tenant):
        if (body.split == "held_out" or existing.data.get("split") == "held_out") and set(existing.data.get("case_fingerprints", [])) & set(fingerprints):
            raise Fault("HOLDOUT_OVERLAP", "Development and held-out task fingerprints must be disjoint", 422)
    data["case_fingerprints"] = fingerprints
    data["digest"] = digest(data)
    record = db.EvaluationDataset(tenant_id=tenant, id=body.id, data=data)
    s.add(record)
    return data


def start(s, tenant, actor, body):
    dataset = db.get(s, db.EvaluationDataset, tenant, body.dataset_id, True)
    if dataset.data["split"] == "held_out" and dataset.status != "active":
        raise Fault("HOLDOUT_CONSUMED", "A held-out dataset is single-use; register a fresh disjoint holdout")
    cases = [Case.model_validate(c) for c in dataset.data["cases"]]
    if len({c.name for c in body.configurations}) != len(body.configurations):
        raise Fault("DUPLICATE_CONFIG", "Configuration names must be unique", 422)
    count = len(cases) * len(body.configurations) * body.repetitions
    if count > 2160:
        raise Fault("EXPERIMENT_LIMIT", "An experiment is limited to 2160 runs", 422)
    upper = sum(Decimal(c.budget.max_cost_usd) for c in cases) * len(body.configurations) * body.repetitions
    if upper > body.max_total_cost_usd:
        raise Fault("EXPERIMENT_BUDGET", "Sum of task hard budgets exceeds the experiment cap", 422)
    for id, expected in dataset.data["project_bindings"].items():
        if digest(db.get(s, db.Project, tenant, id).data) != expected:
            raise Fault("DATASET_DRIFT", "A registered project binding changed")
    if dataset.data["split"] == "held_out":
        dataset.status = "sealed"
    record = db.Evaluation(tenant_id=tenant, status="running", data={})
    s.add(record)
    s.flush()
    jobs = [
        (case, config, repeat) for case in cases for config in body.configurations for repeat in range(body.repetitions)
    ]
    random.Random(body.seed).shuffle(jobs)
    entries = []
    for case, config, repeat in jobs:
        spec = CreateRun(
            project_id=case.project_id,
            title=f"{case.id} / {config.name} / {repeat + 1}",
            task=case.task,
            budget=case.budget,
            model=body.model,
            capabilities=case.capabilities,
            skills=config.skills,
            harness=config.harness,
        )
        run = service.create_run(
            s, tenant, actor, spec, f"experiment:{record.id}:{len(entries)}", evaluation_id=record.id
        )
        entries.append(
            {
                "id": run.id,
                "case_id": case.id,
                "config": config.name,
                "repeat": repeat,
                "project_id": case.project_id,
                "expected_status": case.expected_status,
                "expected_reason": case.expected_reason,
            }
        )
    record.data = {
        **body.model_dump(mode="json"),
        "kind": "paired",
        "dataset": body.dataset_id,
        "dataset_digest": dataset.data["digest"],
        "split": dataset.data["split"],
        "model": "fixture@1" if body.model == "fixture" else settings.model_id,
        "implementation": service.implementation_bindings(),
        "runs": [x["id"] for x in entries],
        "entries": entries,
    }
    return {"id": record.id, "status": record.status, **record.data}


def percentile(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, math.floor((len(values) - 1) * q))] if values else None


def cluster_interval(values, seed):
    # One value per case: repetitions remain together in a sampled task cluster.
    if len(values) < 2:
        return None
    rng = random.Random(seed)
    samples = [mean(rng.choices(values, k=len(values))) for _ in range(1000)]
    return [percentile(samples, 0.025), percentile(samples, 0.975)]


def report(s, record, tenant):
    if record.status == "erased":
        return {"id": record.id, "status": "erased", "limitations": "Knowledge-derived experiment evidence was erased."}
    if record.data.get("report"):
        return record.data["report"]
    results = []
    for entry in record.data["entries"]:
        run = db.get(s, db.Run, tenant, entry["id"])
        ledger = db.get(s, db.BudgetAccount, tenant, run.root_id)
        reason = run.state.get("reason", "")
        correct = run.status == entry["expected_status"] and (
            not entry["expected_reason"] or reason.startswith(entry["expected_reason"])
        )
        results.append(
            {
                **entry,
                "status": run.status,
                "reason": reason,
                "correct_disposition": correct,
                "cost": ledger.spent / 1e6,
                "reserved": ledger.reserved / 1e6,
                "seconds": max(0, (run.updated_at - run.created_at).total_seconds()),
                "verdict": (run.state.get("verification") or {}).get("verdict"),
                "state_digest": digest(run.state),
                "version": run.version,
            }
        )
    finished = all(x["status"] in TERMINAL | {"PAUSED"} for x in results)
    summaries, grouped = {}, defaultdict(list)
    for row in results:
        grouped[(row["config"], row["case_id"])].append(row)
    configs = [c["name"] for c in record.data["configurations"]]
    case_ids = sorted({x["case_id"] for x in results})
    for name in configs:
        rows = [r for r in results if r["config"] == name]
        rates = [mean(r["correct_disposition"] for r in grouped[name, case]) for case in case_ids]
        cost = sum(r["cost"] for r in rows)
        successes = sum(r["status"] == "SUCCEEDED" for r in rows)
        summaries[name] = {
            "correct_disposition_rate": mean(rates),
            "cluster_95_ci": cluster_interval(rates, record.data["seed"]),
            "cost": cost,
            "cost_per_success": cost / successes if successes else None,
            "latency_mean": mean(r["seconds"] for r in rows),
            "latency_p50": percentile([r["seconds"] for r in rows], 0.5),
            "latency_p95": percentile([r["seconds"] for r in rows], 0.95),
        }
    comparisons = {}
    for name in configs[1:]:
        differences = [
            mean(r["correct_disposition"] for r in grouped[name, case])
            - mean(r["correct_disposition"] for r in grouped[configs[0], case])
            for case in case_ids
        ]
        interval = cluster_interval(differences, record.data["seed"])
        baseline_cost = summaries[configs[0]]["cost"]
        ratio = summaries[name]["cost"] / baseline_cost if baseline_cost else None
        supported = finished and interval is not None and interval[0] >= -record.data["noninferiority_margin"]
        comparisons[name] = {
            "baseline": configs[0],
            "paired_difference": mean(differences),
            "cluster_95_ci": interval,
            "cost_ratio": ratio,
            "noninferiority_supported": supported,
        }
    result = {
        "id": record.id,
        "status": "awaiting_reconciliation"
        if finished and any(x["reserved"] for x in results)
        else "completed"
        if finished
        else "running",
        **{k: v for k, v in record.data.items() if k != "report"},
        "results": results,
        "total": len(results),
        "successes": sum(x["status"] == "SUCCEEDED" for x in results),
        "independent_cases": len(case_ids),
        "summaries": summaries,
        "comparisons": comparisons,
        "limitations": "Cluster bootstrap is conditional on registered cases; no external benchmark or safety-effect oracle is implied.",
    }
    # Uncertain charges must stay live so a report cannot hide later reconciled spending.
    if finished and not any(x["reserved"] for x in results):
        record.status = "completed"
        record.data = {**record.data, "report": result}
        for row in results:
            s.add(
                db.EvaluationResult(
                    tenant_id=tenant,
                    id=digest({"evaluation": record.id, "run": row["id"]})[7:],
                    run_id=row["id"],
                    data={"evaluation_id": record.id, **row},
                )
            )
    return result


def release_gate(s, tenant, skill_id, evaluation_id, configuration):
    experiment = db.get(s, db.Evaluation, tenant, evaluation_id, True)
    if experiment.data.get("kind") != "paired":
        raise Fault("SKILL_GATE", "Skill release requires a paired held-out experiment")
    result = report(s, experiment, tenant)
    config = next((c for c in result["configurations"] if c["name"] == configuration), None)
    comparison = result["comparisons"].get(configuration)
    baseline = result["configurations"][0]
    projects = {r["project_id"] for r in result["results"]}
    valid = (
        config
        and skill_id in config["skills"]
        and skill_id not in baseline["skills"]
        and set(config["skills"]) == set(baseline["skills"]) | {skill_id}
        and config["harness"] == baseline["harness"]
        and not baseline["harness"]["memory"]
        and comparison
        and result["split"] == "held_out"
        and result["model"] not in {"", "fixture@1"}
        and result["independent_cases"] >= 20
        and len(projects) >= 2
        and result["repetitions"] >= 3
        and experiment.status == "completed"
        and not any(x["reserved"] for x in result["results"])
        and comparison["noninferiority_supported"]
        and comparison["cost_ratio"] is not None
        and comparison["cost_ratio"] <= result["max_cost_ratio"]
    )
    if not valid:
        raise Fault(
            "SKILL_GATE",
            "Release needs a single-skill comparison with identical harness and memory disabled, settled held-out evidence: 20 cases, two projects, three repetitions, noninferiority and cost gates",
        )
    return digest(result)
