import json
import uuid

import httpx


class ForgeError(Exception):
    def __init__(self, payload, status):
        self.payload, self.status = payload, status
        super().__init__(payload.get("message", str(payload)))


class Client:
    """Thin asynchronous API client. Recovery and authorization remain server responsibilities."""

    def __init__(self, base_url="http://127.0.0.1:8000", token=None, timeout=30):
        self.http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={"Authorization": f"Bearer {token}"} if token else {},
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.http.aclose()

    async def request(self, method, path, body=None, key=None):
        response = await self.http.request(
            method, "/v1" + path, json=body, headers={"Idempotency-Key": key} if key else {}
        )
        if response.is_error:
            raise ForgeError(response.json(), response.status_code)
        return response.json()

    async def create(self, spec, idempotency_key=None):
        return await self.request("POST", "/runs", spec, idempotency_key or str(uuid.uuid4()))

    async def status(self, id):
        return await self.request("GET", "/runs/" + id)

    async def control(self, id, command, reason="CLI request"):
        run = await self.status(id)
        return await self.request(
            "POST", f"/runs/{id}/{command}", {"expected_version": run["stateVersion"], "reason": reason}
        )

    async def events(self, id, after_seq=0):
        async with self.http.stream(
            "GET", f"/v1/runs/{id}/events", headers={"Last-Event-ID": str(after_seq)}, timeout=None
        ) as response:
            if response.is_error:
                await response.aread()
                raise ForgeError(response.json(), response.status_code)
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    yield json.loads(line[6:])
