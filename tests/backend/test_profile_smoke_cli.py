import importlib.util
import json
import os
import subprocess
import sysconfig
from pathlib import Path

from forgeagent.domain import digest
from forgeagent.repository import bundle_from_directory
from forgeagent.sandbox import sandbox
from forgeagent.worker import Worker
from forgeagent.workspace import files

SUITE = Path(__file__).parents[2] / "benchmarks" / "profile-smoke-v1"


async def test_dataset_to_cross_profile_cli(tmp_path, tenant, client, monkeypatch):
    from forgeagent.config import settings

    spec = importlib.util.spec_from_file_location("profile_smoke_fixture", SUITE / "prepare.py")
    suite = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(suite)
    output = tmp_path / "prepared"
    image = "sha256:" + "1" * 64
    suite.prepare(output, image)
    cases = {case["id"]: case for case in suite.repairs()}
    dataset = json.loads((output / "dataset.json").read_bytes())
    experiment = json.loads((output / "experiment.json").read_bytes())
    for case in cases.values():
        directory = output / case["id"]
        contract = json.loads((directory / "acceptance.json").read_bytes())
        build = contract.pop("build")
        bundle = bundle_from_directory(directory / "repository")
        response = client.post("/v1/projects", json={"id": case["id"], "name": case["id"],
            "repository": bundle.model_dump(), "acceptance_id": contract["id"], "acceptance": contract,
            "verification_argv": contract["argv"], "build": build})
        assert response.status_code == 201, response.text
    assert client.post("/v1/evaluation-datasets", json=dataset).status_code == 201
    assert all(c["provenance"]["base_commit"] for c in dataset["cases"])
    validation = json.loads((output / "validation.json").read_bytes())
    assert all(row["baseline_exit"] == 1 and row["reference_exit"] == 0 for row in validation)

    async def image_digest(*args):
        return image

    # This test admits only the exact operator-authored reference files. The local
    # interpreter executes the checked-in oracle against those known files.
    async def verify(root, argv, bound_image, timeout, readonly, build):
        case = next(c for c in cases.values() if c["argv"] == argv)
        reference = {**case["baseline"], **case["fixed"]}
        assert bound_image == image and readonly
        protected = {path: content.replace("\n", os.linesep) for path, content in case["protected_tests"].items()}
        assert files(root) == {**reference, **protected}
        return suite.run_acceptance(case, reference)

    monkeypatch.setattr(sandbox, "image_digest", image_digest)
    monkeypatch.setattr(sandbox, "execute", verify)
    monkeypatch.setattr(settings, "model_provider", "openai")
    monkeypatch.setattr(settings, "input_price", 1)
    monkeypatch.setattr(settings, "output_price", 1)
    reports = []
    for profile in ("scripted-profile-a", "scripted-profile-b"):
        monkeypatch.setattr(settings, "model_id", profile)
        response = client.post("/v1/experiments", json=experiment)
        assert response.status_code == 202, response.text
        record = response.json()
        for trial in record["entries"]:
            case = cases[trial["project_id"]]
            decisions = []
            if trial["case_id"] == "clarify-window":
                decisions.append({"kind": "request_input", "summary": "Which retention window should apply?"})
            elif trial["case_id"] != "token-admission":
                path, content = next(iter(case["fixed"].items()))
                decisions.extend([{"kind": "tool_calls", "summary": "Apply authored fixture change", "calls": [
                    {"tool": "repo.write", "args": {"path": path, "content": content,
                        "expected_digest": digest(case["baseline"][path].encode())}}]},
                    {"kind": "propose_completion", "summary": "Run independent acceptance"}])

            async def scripted(messages, model_id):
                decision = decisions.pop(0)
                return json.dumps(decision), {"input_tokens": 8, "output_tokens": 4}, {}, "simulated"

            run_id = trial["id"]
            worker = Worker(model=scripted, target_run=run_id)
            for _ in range(10):
                await worker.once(tenant)
                run = client.get(f"/v1/runs/{run_id}").json()
                if run["status"] in {"SUCCEEDED", "FAILED", "PAUSED"}:
                    break
            assert run["status"] == trial["expected_status"], (trial, run)
        report = client.get("/v1/evaluations/" + record["id"]).json()
        assert report["status"] == "completed"
        assert all(row["correct_disposition"] for row in report["results"])
        path = tmp_path / (profile + ".json")
        path.write_text(json.dumps(report), encoding="utf8")
        reports.append(str(path))

    executable = Path(sysconfig.get_path("scripts")) / ("forge.exe" if os.name == "nt" else "forge")
    result = subprocess.run([str(executable), "eval", "compare-profiles", *reports], capture_output=True,
                            env={**os.environ, "PYTHONIOENCODING": "ascii"}, timeout=30)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    compared = json.loads(result.stdout)
    assert compared["matched_trials_per_profile"] == len(dataset["cases"]) * 2
    assert len({p["model_profile_digest"] for p in compared["profiles"]}) == 2
    for profile in compared["profiles"]:
        for summary in profile["harnesses"].values():
            assert summary["verified_passes"] == len(cases) and summary["correct_pauses"] == 2
            assert summary["known_model_cost_per_pass_usd"] > 0
