import os

import pytest
from forgeagent.sandbox import sandbox
from forgeagent.verification import verify

pytestmark = pytest.mark.skipif(
    os.getenv("FORGE_TEST_DOCKER") != "1", reason="Requires a working Docker daemon and sandbox image"
)


@pytest.fixture(autouse=True)
def direct_docker_lane(monkeypatch):
    # These exercise the Docker adapter; test_release_integration separately exercises the manager RPC.
    from forgeagent.config import settings

    monkeypatch.setattr(settings, "sandbox_manager_url", "")


async def test_real_container_is_nonroot_readonly_and_network_isolated(tmp_path):
    tmp_path.chmod(0o755)
    (tmp_path / "marker").write_text("immutable")
    image = await sandbox.image_digest()
    code = """import os, socket
assert os.getuid() == 10001
assert open('marker').read() == 'immutable'
try:
    open('marker', 'w').write('changed')
except OSError:
    pass
else:
    raise AssertionError('workspace writable')
try:
    socket.create_connection(('1.1.1.1', 443), timeout=1)
except OSError:
    pass
else:
    raise AssertionError('network reachable')
print('isolation checks passed')
"""
    result = await sandbox.execute(tmp_path, ["python", "-c", code], image, readonly=True)
    assert result["exit_code"] == 0, result
    assert (tmp_path / "marker").read_text() == "immutable"


async def test_real_container_timeout(tmp_path):
    tmp_path.chmod(0o755)
    image = await sandbox.image_digest()
    result = await sandbox.execute(tmp_path, ["python", "-c", "import time; time.sleep(30)"], image, timeout=1)
    assert result["timed_out"] and result["exit_code"] != 0


async def test_real_independent_protected_acceptance(tenant):
    from forgeagent.domain import uid

    contract = {
        "kind": "command",
        "argv": ["python", "-m", "unittest", "discover", "-s", "hidden_tests"],
        "protected_tests": {
            "hidden_tests/test_add.py": "import unittest\nfrom src.calculator import add\nclass Contract(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2,3),5)\n"
        },
    }
    base = {"src/calculator.py": "def add(a,b):\n    return a-b\n"}
    current = {"src/calculator.py": "def add(a,b):\n    return a+b\n"}
    result = await verify(tenant, uid(), 1, base, current, contract, ["src"], await sandbox.image_digest())
    assert result["verdict"] == "PASS", result
