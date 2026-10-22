"""Regression tests for Telegram navigation, credential, assignment, and link-switch fixes."""

from datetime import datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pytest
import redis
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, InaccessibleMessage, Message, Update
from aiogram.types import User as TelegramUser
from sqlalchemy import func, select

from app import workspace as ws
from app.auth import hasher, peek_telegram_login, telegram_user
from app.bot import router, tasks_callback
from app.config import bot_username_key, settings
from app.db import SessionLocal
from app.domain import create_text_task, digest
from app.models import Task, TaskComment, TelegramLoginToken, TelegramSession, User, now
from app.project_bot import assignee_prompt, parse_due, show_projects
from app.telegram_auth import PASSWORD_PROMPT_TTL, password_prompt_key

TOKEN = "123456:synthetic_token_for_tests"


@pytest.fixture
def dispatcher():
    # The bot router can join only one dispatcher, which other test modules share.
    parent = router.parent_router
    if parent is None:
        parent = Dispatcher()
        parent.include_router(router)
    parent.storage.storage.clear()
    return parent


@pytest.fixture
def cache():
    client = redis.Redis.from_url(settings().redis_url)
    yield client
    client.close()


def key(user_id):
    return StorageKey(bot_id=123456, chat_id=user_id, user_id=user_id)


async def feed(dispatcher, session, user_id, text=None, data=None, identifier=100, **message):
    bot = Bot(TOKEN, session=session)
    sender = TelegramUser(id=user_id, is_bot=False, first_name="Tester")
    incoming = Message(
        message_id=identifier,
        date=now(),
        chat=Chat(id=user_id, type="private"),
        text=text if text is not None or message else "Menu",
        from_user=sender,
        **message,
    )
    if data:
        callback = CallbackQuery(
            id=str(identifier), chat_instance="test", data=data, message=incoming, from_user=sender
        )
        await dispatcher.feed_update(bot, Update(update_id=identifier, callback_query=callback))
    else:
        await dispatcher.feed_update(bot, Update(update_id=identifier, message=incoming))


def sent(session, kind="SendMessage"):
    return [call.args[1] for call in session.await_args_list if type(call.args[1]).__name__ == kind]


def buttons(method):
    markup = getattr(method, "reply_markup", None)
    return [button for row in getattr(markup, "inline_keyboard", []) for button in row]


def account(telegram_id):
    with SessionLocal() as db:
        return db.scalar(select(User).where(User.telegram_id == telegram_id))


# Bug 1: navigation ends the active conversation.


@pytest.mark.parametrize(
    "navigation",
    [
        "menu:due",
        "menu:projects",
        "due:0",
        "projects:0",
        "project",
        "projectpage",
        "task",
        "taskstatus",
        "tasks:0",
    ],
)
async def test_navigating_away_ends_comment_conversation(dispatcher, navigation):
    pm = account(100)
    project = ws.create_project(pm, "Navigation")
    first = create_text_task(100, "Alex", 100, 600, "Task A")
    second = create_text_task(100, "Alex", 100, 601, "Task B")
    data = {
        "project": f"project:{project['id']}",
        "projectpage": f"projectpage:{project['id']}:0",
        "task": f"task:{second}",
        "taskstatus": f"taskstatus:{second}:in_progress",
    }.get(navigation, navigation)
    session = AsyncMock(return_value=SimpleNamespace())
    await feed(dispatcher, session, 100, data=f"taskcomment:{first}")
    assert await dispatcher.storage.get_state(key(100)) == "WorkFlow:comment"
    await feed(dispatcher, session, 100, data=data, identifier=101)
    assert await dispatcher.storage.get_state(key(100)) is None
    await feed(dispatcher, session, 100, text="Buy milk", identifier=102)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(TaskComment)) == 0
        # Text outside a conversation is a new personal task, as documented.
        assert db.scalar(select(Task).where(Task.text == "Buy milk")) is not None


async def test_reported_comment_due_open_type_scenario(dispatcher):
    first = create_text_task(100, "Alex", 100, 600, "Task A")
    second = ws.create_task(account(100), "Task B", due_at=now() + timedelta(days=1))
    session = AsyncMock(return_value=SimpleNamespace())
    await feed(dispatcher, session, 100, data=f"taskcomment:{first}")
    await feed(dispatcher, session, 100, data="menu:due", identifier=101)
    await feed(dispatcher, session, 100, data=f"task:{second['id']}", identifier=102)
    await feed(dispatcher, session, 100, text="Buy milk", identifier=103)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(TaskComment)) == 0


async def test_step_buttons_keep_their_conversation(dispatcher):
    session = AsyncMock(return_value=SimpleNamespace())
    await feed(dispatcher, session, 100, data="menu:newtask")
    await feed(dispatcher, session, 100, data="tasknew:personal", identifier=101)
    await feed(dispatcher, session, 100, text="Headline", identifier=102)
    assert await dispatcher.storage.get_state(key(100)) == "WorkFlow:task_content"
    await feed(dispatcher, session, 100, data="tasknew:nodescription", identifier=103)
    assert await dispatcher.storage.get_state(key(100)) == "WorkFlow:task_due"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 0
    picker = (await dispatcher.storage.get_data(key(100)))["deadline_picker"]
    await feed(dispatcher, session, 100, data=f"dl:{picker['token']}:none", identifier=104)
    with SessionLocal() as db:
        assert db.scalar(select(Task)).title == "Headline"


# Bug 2: password messages are removed even after the login state expires.


async def start_password_step(dispatcher, session):
    await feed(dispatcher, session, 500, text="/login", identifier=10)
    await feed(dispatcher, session, 500, text="test1@hire.me", identifier=11)


def deleted_ids(session):
    return [method.message_id for method in sent(session, "DeleteMessage")]


async def test_late_password_after_state_expiry_is_deleted(dispatcher, cache):
    session = AsyncMock(return_value=SimpleNamespace())
    await start_password_step(dispatcher, session)
    assert 300 < cache.ttl(password_prompt_key(500)) <= PASSWORD_PROMPT_TTL
    dispatcher.storage.storage.clear()  # The five-minute FSM state expired.
    await feed(dispatcher, session, 500, text="hireme1", identifier=12)
    assert 12 in deleted_ids(session)
    assert "timed out" in sent(session)[-1].text and "/login" in sent(session)[-1].text
    assert all("hireme1" not in (method.text or "") for method in sent(session))
    with SessionLocal() as db:
        assert db.get(TelegramSession, 500) is None
        assert db.scalar(select(func.count()).select_from(Task)) == 0
    assert not cache.exists(password_prompt_key(500))
    # Only the first message after the prompt is treated as a password.
    await feed(dispatcher, session, 500, text="hello", identifier=13)
    assert 13 not in deleted_ids(session)
    for stored in cache.scan_iter():
        assert b"hireme1" not in stored
        if cache.type(stored) == b"string":
            assert b"hireme1" not in (cache.get(stored) or b"")


@pytest.mark.parametrize("password", ["/hireme1", "/start", "/login", "/help@taskline_bot"])
async def test_slash_text_in_password_step_is_a_deleted_password_attempt(dispatcher, password):
    session = AsyncMock(return_value=SimpleNamespace())
    await start_password_step(dispatcher, session)
    await feed(dispatcher, session, 500, text=password, identifier=12)
    assert 12 in deleted_ids(session)
    assert "incorrect" in sent(session)[-1].text
    assert all(password not in (method.text or "") for method in sent(session))
    with SessionLocal() as db:
        assert db.get(TelegramSession, 500) is None


@pytest.mark.parametrize("command", ["/cancel", "/cancel@taskline_bot"])
async def test_cancel_in_password_step_is_not_deleted(dispatcher, cache, command):
    session = AsyncMock(return_value=SimpleNamespace())
    await start_password_step(dispatcher, session)
    await feed(dispatcher, session, 500, text=command, identifier=12)
    assert 12 not in deleted_ids(session)
    assert "Login cancelled" in sent(session)[-1].text
    assert await dispatcher.storage.get_state(key(500)) is None
    assert not cache.exists(password_prompt_key(500))


async def test_successful_password_clears_marker(dispatcher, cache):
    session = AsyncMock(return_value=SimpleNamespace())
    await start_password_step(dispatcher, session)
    await feed(dispatcher, session, 500, text="hireme1", identifier=12)
    assert 12 in deleted_ids(session)
    assert telegram_user(500) is not None
    assert not cache.exists(password_prompt_key(500))


# Bug 3: assignment toggles people instead of replacing everyone.


def third_member(pm, project_id):
    with SessionLocal.begin() as db:
        db.add(
            User(
                email="sam@example.com",
                password_hash=hasher.hash("synthetic"),
                name="Sam",
                role="developer",
            )
        )
    ws.add_member(pm, project_id, "sam@example.com")
    with SessionLocal() as db:
        return db.scalar(select(User).where(User.email == "sam@example.com"))


def assignees(task_id):
    with SessionLocal() as db:
        return set(db.get(Task, task_id).assignee_ids)


async def test_assign_person_toggles_and_keeps_other_assignees(dispatcher):
    pm, developer = account(100), account(200)
    project = ws.create_project(pm, "Team")
    ws.add_member(pm, project["id"], developer.email)
    sam = third_member(pm, project["id"])
    task = ws.create_task(
        pm, "Shared", project_id=project["id"], assignee_ids=[developer.id, sam.id]
    )
    session = AsyncMock(return_value=SimpleNamespace())
    await feed(dispatcher, session, 100, data=f"taskassign:{task['id']}")
    chooser = sent(session)[-1]
    labels = {button.callback_data: button.text for button in buttons(chooser)}
    assert labels[f"assignee:{developer.id}"].startswith("✓ ")
    assert labels[f"assignee:{sam.id}"].startswith("✓ ")
    assert not labels[f"assignee:{pm.id}"].startswith("✓")
    assert {"assignee:none", "assignee:done"} <= set(labels)
    assert all(len(data.encode()) <= 64 for data in labels)

    await feed(dispatcher, session, 100, data=f"assignee:{sam.id}", identifier=101)
    assert assignees(task["id"]) == {developer.id}
    await feed(dispatcher, session, 100, data=f"assignee:{pm.id}", identifier=102)
    assert assignees(task["id"]) == {developer.id, pm.id}
    edited = sent(session, "EditMessageText")[-1]
    marks = {button.callback_data: button.text for button in buttons(edited)}
    assert marks[f"assignee:{pm.id}"].startswith("✓ ")
    assert not marks[f"assignee:{sam.id}"].startswith("✓")
    # Typing an email toggles that person too.
    await feed(dispatcher, session, 100, text=sam.email, identifier=103)
    assert assignees(task["id"]) == {developer.id, pm.id, sam.id}
    await feed(dispatcher, session, 100, data="assignee:none", identifier=104)
    assert assignees(task["id"]) == set()
    await feed(dispatcher, session, 100, data="assignee:done", identifier=105)
    assert await dispatcher.storage.get_state(key(100)) is None
    assert "Assigned to:</b> Unassigned" in sent(session)[-1].text


async def test_assignee_chooser_hints_when_members_are_truncated(monkeypatch):
    people = [
        {"id": f"00000000-0000-0000-0000-{index:012d}", "name": f"P{index}", "email": "p@x.io"}
        for index in range(30)
    ]
    monkeypatch.setattr("app.project_bot.ws.members", Mock(return_value=people))
    state = FSMContext(storage=MemoryStorage(), key=key(100))
    await state.update_data(project_id="00000000-0000-0000-0000-000000000001")
    message = SimpleNamespace(answer=AsyncMock())
    await assignee_prompt(message, account(100), state)
    text = message.answer.call_args.args[0]
    assert "Showing the first 25 of 30 people" in text
    assert len(message.answer.call_args.kwargs["reply_markup"].inline_keyboard) == 27


# Bug 4: a deep link for another account asks before switching.


@pytest.fixture
def web_link(client, cache):
    response = client.post(
        "/api/auth/login", json={"email": "test1@hire.me", "password": "hireme1"}
    )
    assert response.status_code == 200
    cache.set(bot_username_key(), "taskline_test_bot")

    def issue():
        url = urlparse(client.post("/api/auth/telegram-link").json()["url"])
        return parse_qs(url.query)["start"][0][4:]

    return issue


async def open_switch_prompt(dispatcher, session, token):
    await feed(dispatcher, session, 200, text=f"/start web_{token}", identifier=50)
    prompt = sent(session)[-1]
    assert "This link signs Telegram into test1@hire.me" in prompt.text
    assert "currently signed in as test2@hire.me" in prompt.text
    data = {button.text: button.callback_data for button in buttons(prompt)}
    assert set(data) == {"Switch", "Keep"}
    assert all(token not in value and len(value.encode()) <= 64 for value in data.values())
    return data


async def test_deep_link_for_other_account_asks_and_keep_leaves_session(dispatcher, web_link):
    jamie = telegram_user(200)
    token = web_link()
    session = AsyncMock(return_value=SimpleNamespace())
    data = await open_switch_prompt(dispatcher, session, token)
    assert 50 in [method.message_id for method in sent(session, "DeleteMessage")]
    assert telegram_user(200).id == jamie.id
    assert peek_telegram_login(token) is not None  # Not consumed by the prompt.
    await feed(dispatcher, session, 200, data=data["Keep"], identifier=51)
    assert telegram_user(200).id == jamie.id
    assert "not used" in sent(session, "EditMessageText")[-1].text
    # The stored request is discarded, so the old Switch button no longer works.
    await feed(dispatcher, session, 200, data=data["Switch"], identifier=52)
    assert telegram_user(200).id == jamie.id
    assert len(sent(session, "AnswerCallbackQuery")) == 2


async def test_deep_link_switch_consumes_token_only_on_confirmation(dispatcher, web_link):
    token = web_link()
    session = AsyncMock(return_value=SimpleNamespace())
    data = await open_switch_prompt(dispatcher, session, token)
    await feed(dispatcher, session, 200, data=data["Switch"], identifier=51)
    assert telegram_user(200).email == "test1@hire.me"
    assert peek_telegram_login(token) is None
    assert any("Logged in as" in (method.text or "") for method in sent(session))


async def test_deep_link_switch_revalidates_token(dispatcher, web_link):
    token = web_link()
    session = AsyncMock(return_value=SimpleNamespace())
    data = await open_switch_prompt(dispatcher, session, token)
    with SessionLocal.begin() as db:
        db.get(TelegramLoginToken, digest(token)).expires_at = now() - timedelta(seconds=1)
    await feed(dispatcher, session, 200, data=data["Switch"], identifier=51)
    assert telegram_user(200).email == "test2@hire.me"
    assert "expired or was already used" in sent(session)[-1].text


async def test_deep_link_without_session_signs_in_directly(dispatcher, web_link):
    token = web_link()
    session = AsyncMock(return_value=SimpleNamespace())
    await feed(dispatcher, session, 300, text=f"/start web_{token}", identifier=50)
    assert telegram_user(300).email == "test1@hire.me"


# Low-severity fixes.


async def test_expired_skip_description_answers_callback_once(dispatcher):
    session = AsyncMock(return_value=SimpleNamespace())
    await feed(dispatcher, session, 100, data="tasknew:nodescription")
    answers = sent(session, "AnswerCallbackQuery")
    assert len(answers) == 1 and answers[0].text == "This step expired."


@pytest.mark.parametrize("kind", ["inaccessible", "inline", "group"])
async def test_unusable_callbacks_are_answered(dispatcher, kind):
    session = AsyncMock(return_value=SimpleNamespace())
    bot = Bot(TOKEN, session=session)
    sender = TelegramUser(id=100, is_bot=False, first_name="Alex")
    extra = {
        "inaccessible": {
            "message": InaccessibleMessage(chat=Chat(id=100, type="private"), message_id=1)
        },
        "inline": {"inline_message_id": "inline"},
        "group": {
            "message": Message(message_id=1, date=now(), chat=Chat(id=-5, type="group"), text="Old")
        },
    }[kind]
    callback = CallbackQuery(
        id="cb", chat_instance="test", data="menu:tasks", from_user=sender, **extra
    )
    await dispatcher.feed_update(bot, Update(update_id=1, callback_query=callback))
    assert len(sent(session, "AnswerCallbackQuery")) == 1
    assert not sent(session)


async def test_refresh_reports_already_up_to_date(monkeypatch):
    monkeypatch.setattr("app.bot.telegram_tasks", Mock(return_value=([], False)))
    query = SimpleNamespace(
        data="tasks:0",
        answer=AsyncMock(),
        from_user=SimpleNamespace(id=100),
        message=SimpleNamespace(
            edit_text=AsyncMock(
                side_effect=TelegramBadRequest(
                    method=Mock(), message="Bad Request: message is not modified"
                )
            ),
            answer=AsyncMock(),
        ),
    )
    await tasks_callback(query)
    query.answer.assert_awaited_once_with("Already up to date")
    query.message.answer.assert_not_awaited()


@pytest.mark.parametrize("user_id", [100, 500])
async def test_cancel_outside_conversation_is_friendly(dispatcher, user_id):
    session = AsyncMock(return_value=SimpleNamespace())
    await feed(dispatcher, session, user_id, text="/cancel@taskline_bot")
    assert "Nothing to cancel" in sent(session)[-1].text


@pytest.mark.parametrize("cancel", ["/cancel@taskline_bot", "tasknew:cancel"])
async def test_cancel_ends_comment_with_generic_reply(dispatcher, cancel):
    target = create_text_task(100, "Alex", 100, 600, "Task A")
    session = AsyncMock(return_value=SimpleNamespace())
    await feed(dispatcher, session, 100, data=f"taskcomment:{target}")
    if cancel.startswith("/"):
        await feed(dispatcher, session, 100, text=cancel, identifier=101)
    else:
        await feed(dispatcher, session, 100, data=cancel, identifier=101)
    assert sent(session)[-1].text.startswith("Cancelled.")
    assert "Task creation" not in sent(session)[-1].text
    assert await dispatcher.storage.get_state(key(100)) is None


@pytest.mark.parametrize("value", ["2026-10-22", "20261022"])
def test_parse_due_detects_compact_date_only_input(value):
    zone = ZoneInfo(settings().app_timezone)
    assert parse_due(value) == datetime(2026, 10, 22, 23, 59, tzinfo=zone)


def test_parse_due_keeps_times_and_rejects_out_of_range_years():
    zone = ZoneInfo(settings().app_timezone)
    assert parse_due("2026-10-22 10:30") == datetime(2026, 10, 22, 10, 30, tzinfo=zone)
    assert parse_due("-") is None
    for value in ("2200-01-01", "1960-05-05", "0001-01-01"):
        with pytest.raises(ws.WorkspaceError, match="between 1970 and 2100"):
            parse_due(value)
    with pytest.raises(ValueError):
        parse_due("tomorrow")
    assert parse_due("2100-12-31").time() == time(23, 59)


async def test_typed_out_of_range_deadline_shows_reason_and_keeps_step(dispatcher):
    task = ws.create_task(account(100), "Deadline task")
    session = AsyncMock(return_value=SimpleNamespace())
    await feed(dispatcher, session, 100, data=f"taskdue:{task['id']}")
    await feed(dispatcher, session, 100, text="2200-01-01", identifier=101)
    assert "Deadline must be between 1970 and 2100" in sent(session)[-1].text
    assert await dispatcher.storage.get_state(key(100)) == "WorkFlow:edit_due"
    await feed(dispatcher, session, 100, text="20261022", identifier=102)
    with SessionLocal() as db:
        assert db.get(Task, task["id"]).due_at == datetime(
            2026, 10, 22, 23, 59, tzinfo=ZoneInfo(settings().app_timezone)
        )


async def test_project_chooser_paginates(monkeypatch):
    projects = [
        {"id": f"00000000-0000-0000-0000-{index:012d}", "name": f"P{index}", "can_manage": True}
        for index in range(45)
    ]
    monkeypatch.setattr("app.project_bot.ws.list_projects", Mock(return_value=projects))
    message = SimpleNamespace(answer=AsyncMock())

    def shown():
        markup = message.answer.call_args.kwargs["reply_markup"]
        return [button.callback_data for row in markup.inline_keyboard for button in row]

    await show_projects(message, account(100))
    assert "projects:1" in shown()
    assert len([data for data in shown() if data.startswith("project:0")]) == 40
    await show_projects(message, account(100), 1)
    assert "projects:0" in shown() and "projects:2" not in shown()
    assert len([data for data in shown() if data.startswith("project:0")]) == 5
    assert "Page 2" in message.answer.call_args.args[0]
