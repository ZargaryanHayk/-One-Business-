from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, Message
from aiogram.types import User as TelegramUser

from app.auth import consume_telegram_login, telegram_user
from app.config import bot_username_key
from app.db import SessionLocal
from app.domain import dashboard_token, digest, logout_telegram_user
from app.models import LoginSession, TelegramLoginToken, now
from app.telegram_auth import LoginFlow, TelegramAccess


@pytest.fixture
def web_login(client):
    response = client.post(
        "/api/auth/login", json={"email": "test1@hire.me", "password": "hireme1"}
    )
    assert response.status_code == 200
    import redis

    from app.config import settings

    cache = redis.Redis.from_url(settings().redis_url)
    cache.set(bot_username_key(), "taskline_test_bot")
    cache.close()
    return response.json()


def issue(client):
    response = client.post("/api/auth/telegram-link")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    url = urlparse(response.json()["url"])
    assert url.netloc == "t.me" and url.path == "/taskline_test_bot"
    payload = parse_qs(url.query)["start"][0]
    assert payload.startswith("web_") and len(payload) <= 64
    return payload[4:]


def test_link_requires_login_origin_and_available_bot(client, web_login):
    assert (
        client.post(
            "/api/auth/telegram-link", headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    client.cookies.clear()
    assert client.post("/api/auth/telegram-link").status_code == 401


def test_unavailable_bot_does_not_issue_link(client, login):
    login()
    assert client.post("/api/auth/telegram-link").status_code == 503


def test_link_signs_in_same_account_once_and_never_as_web_token(client, web_login):
    token = issue(client)
    with SessionLocal() as db:
        stored = db.get(TelegramLoginToken, digest(token))
        assert stored is not None
        assert stored.token_hash != token
        assert now() < stored.expires_at <= now() + timedelta(minutes=5)
    assert client.post("/api/auth/exchange", json={"token": token}).status_code == 401
    owner = consume_telegram_login(token, 300)
    assert str(owner.id) == web_login["id"]
    assert telegram_user(300).id == owner.id
    assert consume_telegram_login(token, 400) is None
    assert telegram_user(400) is None
    website_token = dashboard_token(100, "Alex").split("#token=")[1]
    assert consume_telegram_login(website_token, 400) is None


def test_link_switches_bot_account_but_keeps_other_chats(client, web_login):
    original = telegram_user(200)
    token = issue(client)
    assert str(consume_telegram_login(token, 200).id) == web_login["id"]
    assert telegram_user(200).id != original.id
    assert str(telegram_user(100).id) == web_login["id"]


@pytest.mark.parametrize("expiry", ["token", "session"])
def test_expired_links_cannot_authenticate(client, web_login, expiry):
    token = issue(client)
    with SessionLocal.begin() as db:
        stored = db.get(TelegramLoginToken, digest(token))
        target = stored if expiry == "token" else db.get(LoginSession, stored.session_hash)
        target.expires_at = now() - timedelta(seconds=1)
    assert consume_telegram_login(token, 300) is None
    assert telegram_user(300) is None


@pytest.mark.parametrize("logout", ["device", "all", "telegram"])
def test_logout_revokes_unused_links(client, web_login, logout):
    token = issue(client)
    if logout == "telegram":
        logout_telegram_user(100)
    else:
        endpoint = "/api/auth/logout-all" if logout == "all" else "/api/auth/logout"
        assert client.post(endpoint).status_code == 200
    with SessionLocal() as db:
        assert db.get(TelegramLoginToken, digest(token)) is None
    assert consume_telegram_login(token, 300) is None


def test_new_click_replaces_previous_link(client, web_login):
    previous = issue(client)
    current = issue(client)
    assert consume_telegram_login(previous, 300) is None
    assert consume_telegram_login(current, 300) is not None


def test_concurrent_consumers_only_sign_in_once(client, web_login):
    token = issue(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda chat: consume_telegram_login(token, chat), [300, 400]))
    assert sum(owner is not None for owner in results) == 1
    assert sum(telegram_user(chat) is not None for chat in [300, 400]) == 1


# Chat 100 already uses the link's account; chat 300 has no session. A different
# signed-in account must confirm first (see test_fix_bot.py).
@pytest.mark.parametrize("chat", [100, 300])
async def test_middleware_handles_link_before_existing_session_or_password_state(
    client, web_login, monkeypatch, chat
):
    token = issue(client)
    incoming = Message(
        message_id=1,
        date=now(),
        chat=Chat(id=chat, type="private"),
        from_user=TelegramUser(id=chat, is_bot=False, first_name="Alex"),
        text=f"/start@taskline_test_bot web_{token}",
    )
    monkeypatch.setattr(Message, "delete", AsyncMock())
    show = AsyncMock()
    monkeypatch.setattr("app.telegram_auth.show_signed_in", show)
    state = FSMContext(
        storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=chat, user_id=chat)
    )
    await state.set_state(LoginFlow.password)
    await state.update_data(email="test2@hire.me")
    handler = AsyncMock()
    await TelegramAccess()(handler, incoming, {"state": state})
    assert str(telegram_user(chat).id) == web_login["id"]
    show.assert_awaited_once()
    handler.assert_not_awaited()
    assert await state.get_state() is None
    assert await state.get_data() == {}


async def test_invalid_link_does_not_replace_current_bot_session(client, web_login, monkeypatch):
    old = telegram_user(200)
    incoming = Message(
        message_id=1,
        date=now(),
        chat=Chat(id=200, type="private"),
        from_user=TelegramUser(id=200, is_bot=False, first_name="Jamie"),
        text="/start web_bad",
    )
    monkeypatch.setattr(Message, "delete", AsyncMock())
    answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    state = SimpleNamespace(clear=AsyncMock())
    handler = AsyncMock()
    await TelegramAccess()(handler, incoming, {"state": state})
    assert telegram_user(200).id == old.id
    assert "expired or was already used" in answer.call_args.args[0]
    handler.assert_not_awaited()
