import ast
import asyncio
import fnmatch
import shlex
import time
from typing import Any

from .domain import Fault, digest, now
from .sandbox import sandbox
from .state_types import ActionReceipt
from .telemetry import observed
from .workspace import body, text

CONFIG_NAMES = {"pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg", "conftest.py", "package.json",
                "package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock", "uv.lock",
                "requirements.txt", "requirements.lock", ".npmrc", ".yarnrc.yml", "Cargo.lock", "Cargo.toml"}


def protected(path, contract, build):
    name = path.split("/")[-1]
    return (name in CONFIG_NAMES or name.startswith(("jest.config.", "vitest.config.", "tsconfig.", "webpack.config."))
            or path.startswith((".github/", ".gitlab/")) or path == build.get("lockfile")
            or any(fnmatch.fnmatchcase(path, pattern) for pattern in contract.get("protected_paths", [])))


@observed("verification.execute")
async def verify(tenant, run_id, epoch, baseline, current, contract, allowed_paths, image, repository=None,
                 build=None, executor=None, metadata=None):
    started_at, started = now(), time.monotonic()
    build = build or {}
    executor = executor or sandbox.execute
    checks: list[dict[str, Any]] = []
    changed = [p for p in set(baseline) | set(current) if baseline.get(p) != current.get(p)]
    scope_ok = all(any(p == a or p.startswith(a.rstrip("/") + "/") for a in allowed_paths) for p in changed)
    checks.append(
        {
            "name": "修改范围",
            "status": "passed" if scope_ok else "failed",
            "evidence": ", ".join(changed) or "No changes",
        }
    )
    # Existing tests and protected configuration must not be weakened by the generator.
    protected_ok = all(baseline.get(p) == current.get(p) for p in set(baseline) | set(current)
                       if protected(p, contract, build) or (p in baseline and p.startswith(("tests/", "test/"))))
    checks.append(
        {
            "name": "验收配置与既有测试保护",
            "status": "passed" if protected_ok else "failed",
            "evidence": "Baseline comparison",
        }
    )
    before = digest(current)
    result: ActionReceipt | None = None
    verdict = "INCONCLUSIVE"
    build_result = None
    if not scope_ok or not protected_ok:
        verdict = "FAIL"
    if contract.get("kind") == "fixture_ast":
        # Trusted deterministic fixture: inspect syntax; never eval or exec generated code on the host.
        try:
            tree = ast.parse(text(current, "src/calculator.py"))
            expected = ast.parse("def add(left, right):\n    return left + right\n")
            passed = ast.dump(tree) == ast.dump(expected)
        except (SyntaxError, IndexError, AttributeError):
            passed = False
        result = {
            "exit_code": 0 if passed else 1,
            "output": "AST contract: add(left,right) returns left + right",
            "truncated": False,
        }
    elif contract.get("argv") and image and scope_ok and protected_ok:
        def prepare_workspace():
            root = sandbox.restore(tenant, run_id, f"verify-{epoch}", current, repository)
            for name, content in contract.get("protected_tests", {}).items():
                from .sandbox import safe_path

                path = safe_path(root, name)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            return root

        try:
            root = await asyncio.to_thread(prepare_workspace)
            if build.get("ecosystem", "none") != "none":
                lock_ok = (build["lockfile"] in current and digest(body(current[build["lockfile"]])) == build["lock_digest"]
                           and build["dependency_image"] == image)
                if not lock_ok:
                    raise Fault("DEPENDENCY_LOCK_MISMATCH", "Dependency lock or environment differs from registered baseline")
            independent_build = {**build, "persistent_session": False}
            if build.get("argv"):
                # Both commands run in one independent Linux container so bounded
                # /cache build outputs remain available to acceptance, without shell
                # interpretation of any individual argument or reuse of the agent session.
                argv = ["/bin/sh", "-c", shlex.join(build["argv"]) + " && " + shlex.join(contract["argv"])]
                result = await executor(root, argv, image, min(300, build.get("timeout", 120) + contract.get("timeout", 120)),
                                        readonly=True, build=independent_build)
                build_result = {"combined_with_acceptance": True, "argv": build["argv"], "exit_code": result["exit_code"]}
            else:
                result = await executor(root, contract["argv"], image, contract.get("timeout", 120), readonly=True, build=independent_build)
        except Fault as exc:
            result = {"exit_code": -1, "output": exc.message, "error_code": exc.code}
            if exc.code == "DEPENDENCY_LOCK_MISMATCH":
                protected_ok = False
        # Verification runs in a read-only copy; no generated code can alter the accepted artifact.
    if result:
        successes, failures = contract.get("success_exit_codes", [0]), contract.get("failure_exit_codes", [1])
        if result.get("timed_out") or result.get("truncated") or result["exit_code"] not in successes + failures:
            verdict = "INCONCLUSIVE"
        else:
            verdict = "PASS" if result["exit_code"] in successes and scope_ok and protected_ok else "FAIL"
        for condition in contract.get("conditions", []):
            kind, path, value = condition["kind"], condition.get("path"), condition.get("value")
            passed = (path in current if kind == "file_exists" else
                      path in current and digest(body(current[path])) == value if kind == "file_digest" else
                      value in result.get("output", ""))
            checks.append({"name": "注册验收条件: " + kind, "status": "passed" if passed else "failed",
                           "evidence": path or "Registered output condition"})
            if not passed and verdict != "INCONCLUSIVE":
                verdict = "FAIL"
        if contract.get("protected_tests"):
            # Generated code may echo test bodies or assertion answers into stdout. Keep that channel private.
            result = {**result, "output": "Protected acceptance passed" if verdict == "PASS"
                      else "Protected acceptance did not pass; inspect public tests and task constraints",
                      "protected_output": True}
            if build_result:
                build_result = {**build_result, "output": "Protected build diagnostics are private"}
    if not scope_ok or not protected_ok:
        verdict = "FAIL"
    checks.append(
        {
            "name": "独立验收",
            "status": "passed" if verdict == "PASS" else "failed" if verdict == "FAIL" else "not_run",
            "evidence": result["output"][:4000] if result else "No executable acceptance contract or sandbox",
        }
    )
    return {
        "verdict": verdict,
        "evidence_version": 2,
        "started_at": started_at.isoformat(),
        "completed_at": now().isoformat(),
        "duration_seconds": time.monotonic() - started,
        "task_binding": metadata or {"run_id": run_id, "epoch": epoch},
        "repository": {"commit": repository["commit"], "bundle_digest": repository.get("bundle_ref", {}).get("digest")} if repository else None,
        "dependencies": {"ecosystem": build.get("ecosystem", "none"), "lock_digest": build.get("lock_digest"),
                         "image": image},
        "build_result": build_result,
        "failure_class": "hard_constraint" if not scope_ok or not protected_ok else
                         "environment" if verdict == "INCONCLUSIVE" else
                         "acceptance" if verdict == "FAIL" else None,
        "checks": checks,
        "workspace_digest": before,
        "acceptance_digest": digest({k: v for k, v in contract.items() if k != "protected_tests"}
                                    if contract.get("protected_tests_ref") else contract),
        "verifier_version": "forge-verifier@2",
        "environment_digest": image or "fixture-ast@1",
        "environment": (result or {}).get("sandbox_profile", {"kind": "fixture_ast" if contract.get("kind") == "fixture_ast" else "unavailable"}),
        "commands": contract.get("argv", []),
        "result": result,
        "limitations": ["Only the registered acceptance contract is verified; no production publish is implied."],
    }
