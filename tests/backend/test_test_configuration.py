"""Check live opt-in routing without making provider or infrastructure calls."""
from types import SimpleNamespace

import pytest
from conftest import isolate_model_configuration
from forgeagent.config import settings


@pytest.mark.parametrize(("module", "switch", "preserved"), [
    ("test_live_recovery", "FORGE_TEST_LIVE_RECOVERY", True),
    ("tests.backend.test_live_recovery", "FORGE_TEST_LIVE_RECOVERY", True),
    ("test_release_integration", "FORGE_TEST_RELEASE", True),
    ("test_live_recovery", "FORGE_TEST_RELEASE", False),
    ("test_release_integration", "FORGE_TEST_LIVE_RECOVERY", False),
    ("test_runtime", "FORGE_TEST_LIVE_RECOVERY", False),
    ("test_runtime", "FORGE_TEST_RELEASE", False),
    ("test_live_recovery", None, False),
])
def test_only_explicitly_enabled_live_module_keeps_model_configuration(monkeypatch, module, switch, preserved):
    for name in ("FORGE_TEST_RELEASE", "FORGE_TEST_LIVE_RECOVERY"):
        monkeypatch.delenv(name, raising=False)
    if switch:
        monkeypatch.setenv(switch, "1")
    monkeypatch.setattr(settings, "model_api_key", "not-a-real-key")
    monkeypatch.setattr(settings, "model_stream", True)
    monkeypatch.setattr(settings, "model_token_count", False)
    request = SimpleNamespace(module=SimpleNamespace(__name__=module))
    isolate_model_configuration.__wrapped__(monkeypatch, request)
    assert bool(settings.model_api_key) is preserved
    assert settings.model_stream is preserved
    assert settings.model_token_count is not preserved
