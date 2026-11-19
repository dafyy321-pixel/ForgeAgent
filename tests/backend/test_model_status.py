from forgeagent.api import app
from forgeagent.auth import Identity, identity
from forgeagent.config import settings


def test_members_can_check_model_availability_without_diagnostics(client, tenant, monkeypatch):
    app.dependency_overrides[identity] = lambda: Identity(tenant, "tester", False)
    assert client.get("/v1/diagnostics").status_code == 403
    path = "/v1/workspace/model-status"
    assert client.get(path).json() == {"available": False}
    for field, value in {"model_provider": "openai", "model_id": "example", "model_api_key": "private",
                         "input_price": 1, "output_price": 1}.items():
        monkeypatch.setattr(settings, field, value)
    assert client.get(path).json() == {"available": True}
    monkeypatch.setattr(settings, "input_price", 0)
    assert client.get(path).json() == {"available": False}
