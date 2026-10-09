from forgeagent.coding_cases import cases, run_acceptance
from forgeagent.sandbox import sandbox


def test_authored_python_and_typescript_cases_fail_then_reference_pass():
    for case in cases():
        assert not set(case["baseline"]) & set(case["protected_tests"])
        assert run_acceptance(case, case["baseline"])["exit_code"] == 1
        after = {**case["baseline"], **case["fixed"]}
        assert run_acceptance(case, after)["exit_code"] == 0
        assert sandbox.check_patch(case["baseline"], after, sandbox.patch(case["baseline"], after))["status"] == "passed"
