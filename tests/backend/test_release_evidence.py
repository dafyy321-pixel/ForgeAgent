import importlib.util
from pathlib import Path

import pytest


def module():
    spec = importlib.util.spec_from_file_location("release_evidence", Path(__file__).parents[2] / "scripts/release_evidence.py")
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_release_preflight_fails_without_real_environment(monkeypatch):
    gate = module()
    monkeypatch.delenv("FORGE_MODEL_API_KEY", raising=False)
    with pytest.raises(ValueError, match="FORGE_MODEL_API_KEY"):
        gate.preflight()


def test_release_evidence_rejects_skips_missing_groups_and_failure(tmp_path):
    gate = module()
    path = tmp_path / "junit.xml"
    def document(extra=""):
        path.write_text("<testsuite>" + "".join(f'<testcase name="{name}">{extra}</testcase>' for name in gate.REQUIRED.values()) + "</testsuite>")
    for child in ["<skipped/>", "<error/>", "<failure/>"]:
        document(child)
        with pytest.raises(ValueError, match="without"):
            gate.evidence(path, "a" * 40)
    path.write_text('<testsuite><testcase name="only_fixture"/></testsuite>')
    with pytest.raises(ValueError, match="Missing"):
        gate.evidence(path, "a" * 40)
    document()
    result = gate.evidence(path, "a" * 40)
    assert result["source_commit"] == "a" * 40 and result["test_count"] == len(gate.REQUIRED)
    assert len(result["junit_sha256"]) == len(result["digest"]) == 64
