import asyncio
import ssl
from contextlib import asynccontextmanager
from threading import BoundedSemaphore

import pytest

from forgeagent import concurrency, sandbox_manager
from forgeagent.config import settings
from forgeagent.domain import Fault, uid
from forgeagent.sandbox import sandbox


async def test_manager_uses_one_local_slot_without_database(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "sandbox_runtime", "runsc")
    monkeypatch.setattr(sandbox_manager, "local_slots", BoundedSemaphore(1))
    monkeypatch.setattr(concurrency, "acquire", lambda *args: pytest.fail("manager opened a database slot"))
    root = sandbox.root("demo", uid(), 1)
    root.mkdir(parents=True)
    request = sandbox_manager.Execution(operation=uid(), root=str(root), argv=["pytest"],
        image="sha256:" + "a" * 64, timeout=10)
    entered, finish = asyncio.Event(), asyncio.Event()

    async def execute(*args):
        entered.set()
        await finish.wait()
        return {"exit_code": 0}

    monkeypatch.setattr(sandbox, "execute_with_slot", execute)
    first = asyncio.create_task(sandbox_manager.run_execution(request))
    try:
        ready = asyncio.create_task(entered.wait())
        await asyncio.wait([first, ready], return_when=asyncio.FIRST_COMPLETED)
        if first.done():
            await first
        with pytest.raises(Fault) as busy:
            await sandbox_manager.run_execution(request)
        assert busy.value.code == "SANDBOX_BACKPRESSURE"
    finally:
        ready.cancel()
        finish.set()
        await first
    assert (await sandbox_manager.run_execution(request))["exit_code"] == 0


async def test_shared_worker_holds_database_slot_during_manager_call(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "sandbox_manager_url", "https://manager.example")
    occupied = False

    @asynccontextmanager
    async def admission(resource, limit):
        nonlocal occupied
        assert resource == "sandbox"
        occupied = True
        try:
            yield True
        finally:
            occupied = False

    async def execute(*args):
        assert occupied
        return {"exit_code": 0}

    monkeypatch.setattr(concurrency, "admission", admission)
    monkeypatch.setattr(sandbox_manager, "remote_execute", execute)
    result = await sandbox.execute(tmp_path, ["pytest"], "image")
    assert result["exit_code"] == 0 and not occupied


async def test_manager_clients_load_dedicated_ca(monkeypatch):
    import certifi

    contexts = []
    create_context = ssl.create_default_context

    def load_context(*args, **kwargs):
        context = create_context(*args, **kwargs)
        contexts.append((kwargs.get("cafile"), context))
        return context

    monkeypatch.setattr(ssl, "create_default_context", load_context)
    monkeypatch.setattr(settings, "sandbox_manager_ca_file", certifi.where())
    async with sandbox_manager.manager_client(10) as client:
        assert client.trust_env is False
    path, context = contexts[0]
    assert path == certifi.where() and context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
