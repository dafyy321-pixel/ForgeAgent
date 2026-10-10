"""Offline preparation for a reviewed, paired external-repository retrieval pilot."""

from decimal import Decimal

from .domain import Budget, Fault, Harness, digest
from .evaluations import Case, Configuration, DatasetInput, ExperimentInput


def prepare_plan(prepared, dataset_id, split, budget, repetitions=3):
    if not 20 <= len(prepared) <= 30:
        raise Fault("PILOT_SAMPLE", "Provide 20–30 independent reviewed tasks; repetitions are not new tasks", 422)
    cases = [Case.model_validate({**item["case"], "budget": Budget.model_validate(budget).model_dump(mode="json")}) for item in prepared]
    if any(case.provenance is None for case in cases):
        raise Fault("PILOT_PROVENANCE", "All pilot tasks need reviewed external issue provenance", 422)
    identities, repositories, languages = set(), set(), set()
    for case, item in zip(cases, prepared, strict=True):
        source = case.provenance
        assert source is not None
        project = item["project"]
        if (case.project_id != project["id"] or source.base_commit != project["repository"]["commit"]
            or not project["protected_tests"] or project["build"]["dependency_image"] != item["environment_image"]):
            raise Fault("PILOT_BINDING", "Case, Git commit, protected acceptance and reviewed image must be bound", 422)
        identities.add((source.repository, source.task_id))
        repositories.add(source.repository)
        languages.add(source.language)
    if len(identities) != len(cases) or len(repositories) < 2 or languages != {"python", "typescript"}:
        raise Fault("PILOT_INDEPENDENCE", "Use unique issues across repositories and both Python/TypeScript", 422)
    dataset = DatasetInput(id=dataset_id, split=split, source="Reviewed external issue pilot", cases=cases)
    baseline = Harness(planning=False, summarization=False, delegation=False, memory=False, action_fusion=False)
    upper = sum(Decimal(case.budget.max_cost_usd) for case in cases) * 2 * repetitions
    experiment = ExperimentInput(dataset_id=dataset_id, model="configured", repetitions=repetitions,
        configurations=[Configuration(name="single_agent", harness=baseline),
                        Configuration(name="structure", harness=baseline.model_copy(update={"code_retrieval": "structure"}))],
        max_total_cost_usd=upper)
    return {"dataset": dataset.model_dump(mode="json"), "experiment": experiment.model_dump(mode="json"),
            "bindings_digest": digest(prepared), "independent_tasks": len(identities),
            "model_run": "not_run", "sandbox_run": "not_run", "launch_authorized": False,
            "limitations": ["Source and partition labels are reviewed declarations, not proof of statistical independence.",
            "Prepare does not execute imported code or make model calls.",
            "Pin and verify dependency images, license, baseline failure and holdout isolation before launching."]}
