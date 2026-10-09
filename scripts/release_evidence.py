"""Fail-closed release configuration and JUnit evidence validation."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

REQUIRED = {
    "model": "test_live_model_completes_with_settled_provider_usage",
    "gvisor-manager": "test_live_gvisor_manager_enforces_isolation",
    "protocols": "test_live_protocol_negotiation_and_isolated_probes",
    "s3": "test_real_s3_multipart_integrity_and_all_version_erasure",
    "joint-restore": "test_real_postgres_and_object_snapshot_restore_in_isolated_databases",
    "docker": "test_real_container_is_nonroot_readonly_and_network_isolated",
}


def preflight():
    required = ["FORGE_MODEL_API_KEY", "FORGE_MODEL_ID", "FORGE_MODEL_PROVIDER", "FORGE_INPUT_PRICE", "FORGE_OUTPUT_PRICE",
        "FORGE_CACHED_INPUT_PRICE", "FORGE_CACHE_WRITE_PRICE", "FORGE_DATABASE_URL",
        "FORGE_S3_BUCKET", "FORGE_S3_ENDPOINT", "FORGE_TEST_S3_BUCKET_CONFIRM", "FORGE_BACKUP_DATABASE_URL",
        "FORGE_RELEASE_CONNECTIONS_FILE", "FORGE_SANDBOX_MANAGER_URL", "FORGE_SANDBOX_MANAGER_SECRET"]
    missing = [key for key in required if not os.environ.get(key)]
    flags = ["FORGE_TEST_RELEASE", "FORGE_TEST_DOCKER", "FORGE_TEST_S3", "FORGE_TEST_BACKUP"]
    missing += [key for key in flags if os.environ.get(key) != "1"]
    if os.environ.get("FORGE_TEST_S3_BUCKET_CONFIRM") != os.environ.get("FORGE_S3_BUCKET"):
        missing.append("isolated S3 bucket confirmation")
    if missing:
        raise ValueError("Release infrastructure unavailable: " + ", ".join(missing))


def evidence(junit, commit):
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Expected a full source commit SHA")
    root = ET.parse(junit).getroot()
    cases = list(root.iter("testcase"))
    if not cases or any(list(case.iter("failure")) or list(case.iter("error")) or list(case.iter("skipped")) for case in cases):
        raise ValueError("Release tests must pass without errors, failures or skips")
    names = {case.attrib["name"] for case in cases}
    absent = set(REQUIRED.values()) - names
    if absent:
        raise ValueError("Missing live release evidence: " + ", ".join(sorted(absent)))
    now = datetime.now(UTC).isoformat()
    result = {"schema": 1, "source_commit": commit, "created_at": now, "groups": REQUIRED,
        "test_count": len(cases), "junit_sha256": hashlib.sha256(Path(junit).read_bytes()).hexdigest(),
        "workflow_run": os.environ.get("GITHUB_RUN_ID"), "evidence_kind": "live-integration",
        "limitations": "Evidence applies only to the tested commit, provider profile, endpoints and environment."}
    result["digest"] = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--junit", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.junit:
        preflight()
    else:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        result = evidence(args.junit, commit)
        facts = {}
        for name in ["model", "sandbox", "protocols"]:
            value = json.loads((Path(os.environ["FORGE_RELEASE_FACTS_DIR"]) / (name + ".json")).read_text(encoding="utf-8"))
            if value.pop("source_commit") != commit:
                raise ValueError("Live evidence belongs to a different source commit")
            facts[name] = value
        # Model profile contains an endpoint locator: bind it by digest without disclosing it in public artifacts.
        profile = facts["model"].pop("profile")
        facts["model"]["profile_digest"] = hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()
        result["environment"] = facts
        result.pop("digest")
        result["digest"] = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if not args.output:
            parser.error("Evidence output is required")
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(result, output, indent=2)
