import asyncio
import ssl
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from threading import BoundedSemaphore

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
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


async def test_manager_clients_load_dedicated_ca(monkeypatch, tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Forge local TLS test")])
    now = datetime.now(UTC)
    certificate = (x509.CertificateBuilder().issuer_name(name).subject_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(minutes=5))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ip_address("127.0.0.1"))]), critical=False)
        .sign(key, hashes.SHA256()))
    cert_file, key_file = tmp_path / "manager.crt", tmp_path / "manager.key"
    cert_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert_file, key_file)

    async def response(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-length: 2\r\nConnection: close\r\n\r\nok")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(response, "127.0.0.1", 0, ssl=tls)
    async with server:
        url = f"https://127.0.0.1:{server.sockets[0].getsockname()[1]}/"
        monkeypatch.setattr(settings, "sandbox_manager_ca_file", cert_file)
        async with sandbox_manager.manager_client(5) as client:
            assert client.trust_env is False
            assert (await client.get(url)).text == "ok"
        monkeypatch.setattr(settings, "sandbox_manager_ca_file", None)
        async with sandbox_manager.manager_client(5) as client:
            with pytest.raises(httpx.ConnectError, match="CERTIFICATE_VERIFY_FAILED"):
                await client.get(url)
