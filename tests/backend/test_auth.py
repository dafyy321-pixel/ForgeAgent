import pytest
from fastapi import Request
from forgeagent.auth import identity
from forgeagent.config import settings
from forgeagent.domain import Fault


def test_local_identity_rejects_dns_rebinding_host(monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "local")
    request = Request(
        {
            "type": "http",
            "scheme": "http",
            "path": "/v1/workspace",
            "query_string": b"",
            "headers": [(b"host", b"attacker.example"), (b"sec-fetch-site", b"same-origin")],
            "client": ("127.0.0.1", 40000),
        }
    )
    with pytest.raises(Fault, match="Host header"):
        identity(request)


def test_local_identity_accepts_actual_loopback_host(monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "local")
    request = Request(
        {
            "type": "http",
            "scheme": "http",
            "path": "/v1/workspace",
            "query_string": b"",
            "headers": [(b"host", b"127.0.0.1:8000")],
            "client": ("127.0.0.1", 40000),
        }
    )
    assert identity(request).tenant == "local"
