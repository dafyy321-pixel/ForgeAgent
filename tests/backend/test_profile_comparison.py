from copy import deepcopy

import pytest
from forgeagent.domain import Fault


def paired(profile):
    return {"kind": "paired", "model": "fixed-model", "model_profile_digest": profile,
            "id": profile, "dataset_digest": "ds", "implementation": {"state": "v1"},
            "configurations": [{"name": "baseline"}, {"name": "candidate"}],
            "repetitions": 1, "seed": 42, "cost_assumptions": {},
            "status": "completed", "results": [
                {"config": name, "case_id": "bug", "repeat": 0, "status": "SUCCEEDED", "verdict": "PASS"}
                for name in ("baseline", "candidate")],
            "summaries": {name: {"repair_rate": 1, "correct_pauses": 0, "manual_decisions": 0,
                "all_attempts_cost_usd": 2, "latency_p95": 4, "research": {"full_cost_usd": None}}
                for name in ("baseline", "candidate")}, "comparisons": {"candidate": {"negative_transfer_cases": []}}}


def test_profile_comparison_pairs_trials_and_retains_unknown_cost():
    from forgeagent.evaluations import compare_profiles

    first, second = paired("p1"), paired("p2")
    second["results"][1].update(status="PAUSED", verdict=None)
    second["summaries"]["candidate"]["repair_rate"] = 0
    result = compare_profiles([first, second])
    assert len(result["profiles"]) == 2
    assert result["profiles"][0]["harnesses"]["candidate"]["known_model_cost_per_pass_usd"] == 2
    assert result["profiles"][1]["harnesses"]["candidate"]["full_cost_per_pass_usd"] is None
    assert result["profiles"][1]["harnesses"]["candidate"]["known_model_cost_per_pass_usd"] is None
    assert result["profiles"][1]["harnesses"]["candidate"]["verified_passes"] == 0
    wrong = deepcopy(second)
    wrong["results"].pop()
    with pytest.raises(Fault, match="trials"):
        compare_profiles([first, wrong])
    with pytest.raises(Fault, match="profile"):
        compare_profiles([first, first])
    second["dataset_digest"] = "another"
    with pytest.raises(Fault, match="protocol"):
        compare_profiles([first, second])
