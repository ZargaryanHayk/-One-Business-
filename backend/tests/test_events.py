import asyncio
import json
import socket
import threading
import time

import httpx
import redis
import uvicorn

from app.api import app
from app.config import settings
from app.dispatcher import dispatch_once
from app.domain import create_text_task, dashboard_token


async def test_sse_user_isolation_and_session_revocation(monkeypatch):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    assert server.started
    cache = redis.Redis.from_url(settings().redis_url)
    monkeypatch.setattr("app.dispatcher.celery.send_task", lambda *args, **kwargs: None)
    try:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", headers={"Origin": settings().app_origin}
        ) as client:
            token = dashboard_token(100, "Alex").split("#token=")[1]
            assert (
                await client.post("/api/auth/exchange", json={"token": token})
            ).status_code == 200
            async with client.stream("GET", "/api/events", timeout=10) as response:
                assert response.status_code == 200
                lines = response.aiter_lines()
                assert await anext(lines) == "event: ready"
                other = await asyncio.to_thread(create_text_task, 200, "Jamie", 200, 1, "Private")
                own = await asyncio.to_thread(create_text_task, 100, "Alex", 100, 1, "My task")
                await asyncio.to_thread(dispatch_once, cache)
                async with asyncio.timeout(5):
                    async for line in lines:
                        if line.startswith("data: ") and "task_id" in line:
                            payload = json.loads(line.removeprefix("data: "))
                            assert payload["task_id"] == str(own)
                            assert payload["task_id"] != str(other)
                            break
                assert (await client.post("/api/auth/logout")).status_code == 200
                async with asyncio.timeout(8):
                    async for line in lines:
                        if line == "event: signed_out":
                            break
                    else:
                        raise AssertionError("SSE did not terminate after session revocation")
    finally:
        server.should_exit = True
        await asyncio.to_thread(thread.join, 10)
        listener.close()
        cache.close()
