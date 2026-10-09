import asyncio
import inspect
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
        self.token = token
        self.http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={"Authorization": f"Bearer {token}"} if token and not callable(token) else {},
        )

    async def headers(self):
        token = self.token() if callable(self.token) else self.token
        if inspect.isawaitable(token):
            token = await token
        return {"Authorization": "Bearer " + token} if token else {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.http.aclose()

    async def request(self, method, path, body=None, key=None):
        response = await self.http.request(
            method, "/v1" + path, json=body, headers={**await self.headers(), **({"Idempotency-Key": key} if key else {})}
        )
        if response.is_error:
            try:
                payload = response.json()
            except ValueError:
                payload = {"code": "HTTP_ERROR", "message": f"HTTP {response.status_code}"}
            raise ForgeError(payload, response.status_code)
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

    async def events(self, id, after_seq=0, max_reconnects=8):
        cursor, failures = after_seq, 0
        while True:
            try:
                async with self.http.stream("GET", f"/v1/runs/{id}/events",
                    headers={**await self.headers(), "Last-Event-ID": str(cursor)},
                    timeout=httpx.Timeout(30, read=45)) as response:
                    if response.is_error:
                        await response.aread()
                        try:
                            payload = response.json()
                        except ValueError:
                            payload = {"code": "HTTP_ERROR", "message": f"HTTP {response.status_code}"}
                        raise ForgeError(payload, response.status_code)
                    event, event_id, lines = "domain", None, []
                    async for line in response.aiter_lines():
                        if line.startswith("event:"):
                            event = line[6:].strip()
                        elif line.startswith("id:"):
                            event_id = int(line[3:].strip())
                        elif line.startswith("data:"):
                            lines.append(line[5:].lstrip())
                        elif line == "" and lines:
                            value = json.loads("\n".join(lines))
                            if event == "end":
                                return
                            if event in {"auth_expired", "authorization_error"}:
                                if event == "auth_expired" and callable(self.token):
                                    break
                                raise ForgeError(value, 401 if event == "auth_expired" else 403)
                            if event_id is not None and event_id > cursor:
                                cursor = event_id
                                failures = 0
                                yield value
                            event, event_id, lines = "domain", None, []
            except (httpx.TransportError, httpx.TimeoutException):
                pass
            failures += 1
            if failures > max_reconnects:
                raise ForgeError({"code": "STREAM_DISCONNECTED", "message": "Event reconnect limit reached",
                                  "after_seq": cursor}, 503)
            await asyncio.sleep(min(10, .25 * 2 ** (failures - 1)))
