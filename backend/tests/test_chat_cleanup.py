import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import redis
from aiogram.fsm.storage.base import DefaultKeyBuilder, StorageKey
from aiogram.types import Chat, Message
from celery.contrib.testing.worker import start_worker
from sqlalchemy import select

from app.bot import cancel_callback, logout_command
from app.celery_app import celery
from app.chat_cleanup import (
    TrackOutgoingMessages,
    clear_chat,
    record_http_message,
    record_message,
    schedule_cleanup,
)
from app.config import settings
from app.db import SessionLocal
from app.dispatcher import dispatch_once, recover_stale_jobs
from app.jobs import notify
from app.models import ChatCleanup, Outbox, TelegramMessage, now


def response(payload=True, status=200):
    return httpx.Response(
        status,
        json={"ok": status == 200, "result": payload},
        request=httpx.Request("POST", "https://telegram.invalid/test"),
    )


@pytest.fixture
def transport(monkeypatch):
    calls = []

    def send(method, payload):
        calls.append((method, payload))
        if method == "sendMessage":
            return response(
                {
                    "message_id": 2,
                    "date": int(now().timestamp()),
                    "chat": {"id": payload["chat_id"], "type": "private"},
                }
            )
        return response()

    monkeypatch.setattr("app.chat_cleanup.telegram_call", send)
    return calls


def queued(chat_id=100):
    with SessionLocal.begin() as db:
        return schedule_cleanup(db, chat_id)


@pytest.mark.parametrize("all_devices", [False, True])
def test_confirmed_web_logout_queues_scoped_cleanup_and_preserves_future_messages(
    client, login, transport, all_devices
):
    record_message(100, 1, now())
    record_message(100, 2, now())
    record_message(100, 3, now() - timedelta(hours=49))
    record_message(200, 10, now())
    login()
    endpoint = "/api/auth/logout-all" if all_devices else "/api/auth/logout"
    assert client.post(endpoint).status_code == 200
    with SessionLocal() as db:
        job = db.scalar(select(ChatCleanup))
        assert job.chat_id == 100 and set(job.message_ids) == {1, 2}
        identifier = str(job.id)
        assert db.scalar(select(Outbox).where(Outbox.kind == "chat_cleanup")) is not None
    record_message(100, 4, now())  # A new conversation after logout must survive.
    clear_chat(identifier)
    assert transport == [("deleteMessages", {"chat_id": 100, "message_ids": [2, 1]})]
    with SessionLocal() as db:
        assert db.get(ChatCleanup, uuid.UUID(identifier)).completed_at is not None
        assert db.get(TelegramMessage, (100, 4)) is not None
        assert db.get(TelegramMessage, (100, 3)) is not None
        assert db.get(TelegramMessage, (200, 10)) is not None
        assert db.get(TelegramMessage, (100, 1)) is None
    clear_chat(identifier)
    assert len(transport) == 1


def test_cleanup_dismisses_keyboard_removes_its_temporary_message_and_clears_fsm(transport):
    record_message(100, 1, now())
    cache = redis.Redis.from_url(settings().redis_url)
    key = StorageKey(bot_id=123456, chat_id=100, user_id=100)
    builder = DefaultKeyBuilder()
    cache.set(builder.build(key, "state"), "WorkFlow:comment")
    cache.set(builder.build(key, "data"), '{"text":"not a credential"}')
    identifier = queued()
    clear_chat(str(identifier))
    assert not cache.exists(builder.build(key, "state"), builder.build(key, "data"))
    cache.close()
    assert transport[0][0] == "sendMessage"
    assert transport[0][1]["reply_markup"] == {"remove_keyboard": True}
    assert transport[1] == ("deleteMessages", {"chat_id": 100, "message_ids": [2, 1]})
    with SessionLocal() as db:
        assert list(db.scalars(select(TelegramMessage))) == []


def test_cleanup_batches_and_skips_one_undeletable_message(monkeypatch, transport):
    for identifier in range(1, 206):
        record_message(100, identifier, now())
    identifier = queued()
    record_message(100, 500, now())
    clear_chat(str(identifier))
    assert [len(payload["message_ids"]) for method, payload in transport] == [100, 100, 5]
    record_message(100, 501, now())
    identifier = queued()
    record_message(100, 600, now())
    calls = []

    def partial(method, payload):
        calls.append((method, payload))
        return response(
            status=400 if method == "deleteMessages" or payload.get("message_id") == 501 else 200
        )

    monkeypatch.setattr("app.chat_cleanup.telegram_call", partial)
    clear_chat(str(identifier))
    assert [method for method, _ in calls] == ["deleteMessages", "deleteMessage", "deleteMessage"]
    with SessionLocal() as db:
        assert db.get(ChatCleanup, identifier).completed_at is not None
        assert db.get(TelegramMessage, (100, 600)) is not None


def test_cleanup_network_failure_retries_without_restoring_auth(
    client, login, monkeypatch, transport
):
    record_message(100, 1, now())
    login()
    assert client.post("/api/auth/logout-all").status_code == 200
    with SessionLocal() as db:
        identifier = db.scalar(select(ChatCleanup.id))
    record_message(100, 2, now())
    monkeypatch.setattr(
        "app.chat_cleanup.telegram_call", Mock(side_effect=httpx.ConnectError("offline"))
    )
    with pytest.raises(httpx.ConnectError):
        clear_chat(str(identifier))
    assert client.get("/api/me").status_code == 401
    with SessionLocal() as db:
        assert db.get(ChatCleanup, identifier).message_ids == [1]
        db.close()
    monkeypatch.setattr("app.chat_cleanup.telegram_call", lambda *args: response())
    clear_chat(str(identifier))
    with SessionLocal() as db:
        assert db.get(ChatCleanup, identifier).completed_at is not None


async def test_prompt_and_cancel_do_not_schedule_cleanup():
    message = SimpleNamespace(answer=AsyncMock())
    await logout_command(message)
    callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(edit_text=AsyncMock()))
    await cancel_callback(callback)
    with SessionLocal() as db:
        assert list(db.scalars(select(ChatCleanup))) == []


async def test_outgoing_tracking_stores_only_message_metadata():
    message = Message(
        message_id=77,
        date=now(),
        chat=Chat(id=100, type="private"),
        text="not persisted in tracking",
    )
    middleware = TrackOutgoingMessages()
    result = await middleware(AsyncMock(return_value=message), None, None)
    assert result == message
    record_message(100, 77, now())
    record_message(-100, 1, now())
    record_http_message(
        response(
            {
                "message_id": 78,
                "date": int(now().timestamp()),
                "chat": {"id": 100, "type": "private"},
            }
        )
    )
    with SessionLocal() as db:
        rows = list(db.scalars(select(TelegramMessage)))
        assert {item.message_id for item in rows} == {77, 78}
        assert not hasattr(rows[0], "text")


def test_old_notifications_cannot_repopulate_a_cleared_chat(client, login, monkeypatch):
    login()
    created_at = now().isoformat()
    assert client.post("/api/auth/logout").status_code == 200
    send = Mock()
    monkeypatch.setattr("app.jobs.httpx.post", send)
    notify.run(100, "Old confirmation", created_at=created_at)
    assert not send.called


def test_cleanup_outbox_redis_worker_and_recovery(client, login, transport):
    import time

    record_message(100, 1, now())
    login()
    client.post("/api/auth/logout-all")
    with SessionLocal() as db:
        identifier = db.scalar(select(ChatCleanup.id))
    record_message(100, 2, now())
    cache = redis.Redis.from_url(settings().redis_url)
    try:
        with start_worker(celery, queues=["notifications"], pool="solo", perform_ping_check=False):
            assert dispatch_once(cache) == 1
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                with SessionLocal() as db:
                    if db.get(ChatCleanup, identifier).completed_at:
                        break
                time.sleep(0.05)
            else:
                pytest.fail("Cleanup did not pass through outbox, Redis, and Celery")
        assert transport == [("deleteMessages", {"chat_id": 100, "message_ids": [1]})]
        identifier = queued()
        with SessionLocal.begin() as db:
            db.get(ChatCleanup, identifier).updated_at = now() - timedelta(minutes=21)
        assert recover_stale_jobs() == 1
        assert recover_stale_jobs() == 0
    finally:
        cache.close()
