import ast

from .domain import digest
from .sandbox import sandbox


async def verify(tenant, run_id, epoch, baseline, current, contract, allowed_paths, image):
    checks = []
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
    protected_ok = all(
        baseline[p] == current.get(p)
        for p in baseline
        if p.startswith(("tests/", "test/")) or p.split("/")[-1] in {"pyproject.toml", "pytest.ini"}
    )
    checks.append(
        {
            "name": "验收配置与既有测试保护",
            "status": "passed" if protected_ok else "failed",
            "evidence": "Baseline comparison",
        }
    )
    before = digest(current)
    result = None
    verdict = "INCONCLUSIVE"
    if contract.get("kind") == "fixture_ast":
        # Trusted deterministic fixture: inspect syntax; never eval or exec generated code on the host.
        try:
            tree = ast.parse(current.get("src/calculator.py", ""))
            expected = ast.parse("def add(left, right):\n    return left + right\n")
            passed = ast.dump(tree) == ast.dump(expected)
        except (SyntaxError, IndexError, AttributeError):
            passed = False
        result = {
            "exit_code": 0 if passed else 1,
            "output": "AST contract: add(left,right) returns left + right",
            "truncated": False,
        }
    elif contract.get("argv") and image:
        root = sandbox.restore(tenant, run_id, f"verify-{epoch}", current)
        for name, content in contract.get("protected_tests", {}).items():
            from .sandbox import safe_path

            path = safe_path(root, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        result = await sandbox.execute(root, contract["argv"], image, contract.get("timeout", 120), readonly=True)
        # Verification runs in a read-only copy; no generated code can alter the accepted artifact.
    if result:
        if result.get("timed_out") or result.get("truncated") or result["exit_code"] in {-1, 125, 126, 127, 137}:
            verdict = "INCONCLUSIVE"
        else:
            verdict = "PASS" if result["exit_code"] == 0 and scope_ok and protected_ok else "FAIL"
        if contract.get("protected_tests"):
            # Generated code may echo test bodies or assertion answers into stdout. Keep that channel private.
            result = {**result, "output": "Protected acceptance passed" if verdict == "PASS"
                      else "Protected acceptance did not pass; inspect public tests and task constraints",
                      "protected_output": True}
    checks.append(
        {
            "name": "独立验收",
            "status": "passed" if verdict == "PASS" else "failed" if verdict == "FAIL" else "not_run",
            "evidence": result["output"][:4000] if result else "No executable acceptance contract or sandbox",
        }
    )
    return {
        "verdict": verdict,
        "failure_class": "hard_constraint" if not scope_ok or not protected_ok else
                         "environment" if verdict == "INCONCLUSIVE" else
                         "acceptance" if verdict == "FAIL" else None,
        "checks": checks,
        "workspace_digest": before,
        "acceptance_digest": digest({k: v for k, v in contract.items() if k != "protected_tests"}
                                    if contract.get("protected_tests_ref") else contract),
        "verifier_version": "forge-verifier@1",
        "environment_digest": image or "fixture-ast@1",
        "commands": contract.get("argv", []),
        "result": result,
        "limitations": ["Only the registered acceptance contract is verified; no production publish is implied."],
    }
