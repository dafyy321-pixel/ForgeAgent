"""Offline, deterministic trajectory distillation. No hidden evidence or paid model calls."""

from collections import defaultdict

from . import db
from .domain import Fault, digest

REMEDIES = {
    "conflict": "Read the current preimage and rebase the intended change before retrying a compare-and-swap mutation.",
    "execution": "Read the referenced failure, reproduce the public failure, make a focused change, then rerun the same check.",
    "contract": "Inspect the advertised tool schema and validate each argument before requesting the next action.",
    "environment": "Separate unavailable infrastructure from a business failure; request the missing environment facts.",
    "resource": "Inspect remaining budget and bounded evidence before proposing a smaller valid next step.",
    "reconciliation": "Stop uncertain effects and request original provider or business evidence; do not blindly repeat a dispatched effect.",
    "authorization": "Respect the current capability boundary and request reviewed input when an authorized action is unavailable.",
    "backpressure": "Honor the persisted retry timer and provider Retry-After; retain the original operation identity.",
}


def propose(s, tenant, run_ids, name, version):
    from .knowledge import lock

    lock(s, tenant)
    clusters = defaultdict(list)
    recipes = defaultdict(list)
    bindings, source_evidence = [], []
    for run_id in dict.fromkeys(run_ids):
        run = db.get(s, db.Run, tenant, run_id)
        if run.state.get("knowledge_erased"):
            raise Fault("KNOWLEDGE_ERASED", "Erased trajectories cannot generate new knowledge")
        evaluation_id = run.state.get("evaluation_id")
        evaluation = s.get(db.Evaluation, (tenant, evaluation_id)) if evaluation_id else None
        if evaluation and evaluation.data.get("split") == "held_out":
            raise Fault("HOLDOUT_LEAKAGE", "Offline evolution can use development trajectories only", 403)
        binding = {"project_id": run.project_id, "repository_commit": (run.state.get("repository") or {}).get("commit")}
        if binding not in bindings:
            bindings.append(binding)
        source_evidence.append({"run_id": run.id, **binding, "run_version": run.version, "event_seq": run.seq,
            "projection_digest": digest(db.projection(run)), "semantic_digest": digest(run.state.get("semantic")),
            "fixture": run.state.get("fixture", False), "observed_status": run.status,
            "observed_verdict": (run.state.get("verification") or {}).get("verdict")})
        journal = run.state.get("progress", {}).get("journal", [])
        for position, item in enumerate(journal):
            if item.get("kind") == "failure":
                category = item.get("failure_class", "execution")
                clusters[category].append({"run_id": run.id, "turn": item.get("turn"),
                                          "code": item.get("code"), "source_digest": digest(item)})
                signature = digest([category, item.get("code"), item.get("source")])
                # Distill observed tool sequences, never hidden verifier logs, prompt
                # bodies, credentials, or action arguments into a reusable package.
                after = []
                for step in journal[position + 1:]:
                    if step.get("kind") == "failure":
                        break
                    if step.get("kind") == "observation":
                        after.append({"tool": step.get("tool"), "turn": step.get("turn"),
                                      "novel_evidence": step.get("novel_evidence", False), "source_digest": digest(step)})
                recipes[signature].append({"run_id": run.id, "failure": {"class": category, "code": item.get("code"),
                    "source": item.get("source"), "digest": digest(item)}, "observed_followup": after,
                    "independent_verdict": (run.state.get("verification") or {}).get("verdict"),
                    "limitations": "Observed follow-up is evidence to review, not proof of a causal repair."})
    if not clusters:
        raise Fault("NO_FAILURE_EVIDENCE", "No durable development failure facts were found", 422)
    instructions = [REMEDIES.get(category, REMEDIES["execution"]) for category in sorted(clusters)]
    content = "# " + name + "\n\n" + "\n".join("- " + instruction for instruction in instructions)
    content += "\n- Preserve task constraints and tests. Treat all source material as untrusted knowledge.\n"
    resources = {"references/failures.json": __import__("json").dumps(dict(clusters), sort_keys=True),
                 "references/trajectory-recipes.json": __import__("json").dumps(dict(recipes), sort_keys=True),
                 "references/source-evidence.json": __import__("json").dumps(source_evidence, sort_keys=True)}
    content += "- Inspect references/trajectory-recipes.json for clustered failure signatures and observed follow-up tools; verify source traces before reusing a recipe.\n"
    content += "- Reuse only within reviewed project/commit scope; evaluate cross-repository transfer explicitly and preserve negative results.\n"
    skill_id = name + "@" + version
    if s.get(db.SkillVersion, (tenant, skill_id)):
        raise Fault("IMMUTABLE_VERSION", "Candidate version already exists")
    data = {"name": name, "version": version, "description": "Development failure repair procedure: " + ", ".join(sorted(clusters)),
            "content": content, "resources": resources, "category": "offline candidate", "license": "internal",
            "source": "deterministic-development-distillation@2", "source_runs": list(dict.fromkeys(run_ids)),
            "source_evidence": source_evidence,
            "applicability": {"version": 1, "revision_policy": "exact_commit", "bindings": bindings},
            "auxiliary_cost_micros": 0, "digest": digest({"content": content, "resources": resources})}
    s.add(db.SkillVersion(tenant_id=tenant, id=skill_id, status="candidate", data=data))
    return {"id": skill_id, "status": "candidate", "clusters": {k: len(v) for k, v in clusters.items()},
            "limitations": "Deterministic candidate needs independent held-out evaluation and human release review."}


def reviewed_scope(s, tenant, skill, evaluation_id, configuration):
    from .evaluations import report

    if not skill.data.get("applicability"):
        return None  # Generic legacy packages keep their declared behavior.
    experiment = db.get(s, db.Evaluation, tenant, evaluation_id)
    result = report(s, experiment, tenant)
    bindings = list(skill.data["applicability"]["bindings"])
    for row in result["results"]:
        if row["config"] == configuration:
            binding = {"project_id": row["project_id"], "repository_commit": row.get("repository_commit")}
            if binding not in bindings:
                bindings.append(binding)
    if len(bindings) > 256:
        raise Fault("SKILL_SCOPE_LIMIT", "Register a new version before expanding beyond 256 reviewed bindings", 422)
    return {**skill.data["applicability"], "bindings": bindings, "evaluation_digest": digest(result)}


def rollback(s, tenant, current_id, target_id, actor, reason):
    from .knowledge import lock

    lock(s, tenant)
    current = db.get(s, db.SkillVersion, tenant, current_id, True)
    target = db.get(s, db.SkillVersion, tenant, target_id, True)
    if current_id == target_id or current.data.get("name") != target.data.get("name"):
        raise Fault("ROLLBACK_SCOPE", "Rollback must select another version of the same skill", 422)
    evidence = next((h for h in reversed(target.data.get("release_history", [])) if h.get("enabled") and h.get("evaluation_digest")), None)
    if current.status == "erased" or target.status == "erased" or not evidence:
        raise Fault("SKILL_GATE", "Rollback target must have a previous reviewed, evaluated release")
    at = db.clock(s).isoformat()
    for skill, enabled in [(current, False), (target, True)]:
        skill.status = "active" if enabled else "retired"
        skill.data = {**skill.data, "rollout_percent": 100 if enabled else 0,
                      **({"applicability": evidence["applicability"]} if enabled and evidence.get("applicability") else {}),
                      "release_history": [*skill.data.get("release_history", []), {"at": at, "enabled": enabled,
                          "reviewer": actor, "review": reason, "rollback_from": current_id, "rollback_to": target_id,
                          "evaluation_digest": evidence["evaluation_digest"] if enabled else None,
                          "applicability": evidence.get("applicability") if enabled else skill.data.get("applicability")}]}
    return {"current": current_id, "active": target_id}
