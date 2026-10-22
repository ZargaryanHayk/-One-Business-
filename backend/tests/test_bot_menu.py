from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Audio, CallbackQuery, Chat, Message, ReplyKeyboardMarkup, Update, Voice
from aiogram.types import User as TelegramUser
from sqlalchemy import func, select

from app.bot import login_callback, logout_callback, menu_callback, router, tasks_callback
from app.bot_ui import (
    ADD_TASK,
    HELP,
    LOGIN,
    LOGOUT,
    MENU,
    TASKS,
    inline_menu,
    main_keyboard,
    tasks_keyboard,
)
from app.db import SessionLocal
from app.domain import (
    create_text_task,
    dashboard_token,
    digest,
    logout_telegram_user,
    telegram_tasks,
)
from app.models import LoginSession, LoginToken, Task, TelegramSession, User, now


def test_task_pagination_is_private():
    for index in range(8):
        create_text_task(100, "Alex", 100, index, f"Alex's task {index}")
    create_text_task(200, "Jamie", 200, 1, "Jamie's private task")
    first, more = telegram_tasks(100)
    second, last = telegram_tasks(100, 1)
    assert len(first) == 5 and more
    assert len(second) == 3 and not last
    assert not ({task.id for task in first} & {task.id for task in second})
    assert all("Alex's" in task.text for task in first + second)
    with pytest.raises(PermissionError):
        telegram_tasks(300)
    with pytest.raises(ValueError):
        telegram_tasks(100, -1)


def test_telegram_logout_revokes_only_owners_sessions_and_links(client, login):
    alex = login(100, "Alex")
    login(100, "Alex")
    jamie = login(200, "Jamie")
    alex_link = dashboard_token(100, "Alex").split("#token=")[1]
    jamie_link = dashboard_token(200, "Jamie").split("#token=")[1]
    task_id = create_text_task(100, "Alex", 100, 1, "Keep this task")
    assert logout_telegram_user(100) == 2
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(LoginSession)
                .where(LoginSession.user_id == alex.id)
            )
            == 0
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(LoginSession)
                .where(LoginSession.user_id == jamie.id)
            )
            == 1
        )
        assert db.get(LoginToken, digest(alex_link)) is None
        assert db.get(LoginToken, digest(jamie_link)) is not None
        assert db.get(Task, task_id).text == "Keep this task"
    assert client.get("/api/me").status_code == 200  # Jamie remains signed in.
    assert logout_telegram_user(300) == 0


@pytest.fixture(scope="session")
def menu_dispatcher():
    # The bot router can join only one dispatcher, which other test modules share.
    if router.parent_router is not None:
        return router.parent_router
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return dispatcher


@pytest.fixture(autouse=True)
def reset_menu_state(menu_dispatcher):
    menu_dispatcher.storage.storage.clear()


@pytest.mark.parametrize("button", [ADD_TASK, TASKS, LOGIN, LOGOUT, HELP])
async def test_menu_buttons_are_routed_without_creating_tasks(monkeypatch, menu_dispatcher, button):
    create = Mock()
    monkeypatch.setattr("app.bot.create_text_task", create)
    telegram = AsyncMock(return_value=SimpleNamespace())
    bot = Bot("123456:synthetic_token_for_tests", session=telegram)
    message = Message(
        message_id=1,
        date=now(),
        chat=Chat(id=100, type="private"),
        text=button,
        from_user=TelegramUser(id=100, is_bot=False, first_name="Alex"),
    )
    await menu_dispatcher.feed_update(bot, Update(update_id=1, message=message))
    assert not create.called
    assert telegram.await_count == (2 if button == HELP else 1)
    if button == HELP:
        markup = telegram.call_args.args[1].reply_markup
        assert markup == inline_menu()


def query(data):
    return SimpleNamespace(
        data=data,
        answer=AsyncMock(),
        from_user=SimpleNamespace(id=100, full_name="Alex"),
        message=SimpleNamespace(
            text="old",
            edit_text=AsyncMock(),
            edit_reply_markup=AsyncMock(),
            answer=AsyncMock(),
            chat=SimpleNamespace(id=100),
        ),
        bot=SimpleNamespace(set_my_commands=AsyncMock()),
    )


async def test_pagination_callback_uses_clicking_owner(monkeypatch):
    load = Mock(return_value=([], False))
    monkeypatch.setattr("app.bot.telegram_tasks", load)
    callback = query("tasks:1")
    await tasks_callback(callback)
    load.assert_called_once_with(100, 1)
    callback.answer.assert_awaited_once()


async def test_inline_login_uses_clicking_owner(monkeypatch):
    link = Mock(return_value="http://localhost:8080/#token=synthetic")
    monkeypatch.setattr("app.bot.dashboard_token", link)
    callback = query("menu:login")
    await login_callback(callback)
    link.assert_called_once_with(100, "Alex")
    assert callback.message.answer.call_args.kwargs["link_preview_options"].is_disabled


async def test_website_login_deep_link_opens_sign_in(monkeypatch, menu_dispatcher):
    login = Mock(return_value="http://localhost:8080/#token=synthetic")
    monkeypatch.setattr("app.bot.dashboard_token", login)
    telegram = AsyncMock(return_value=SimpleNamespace())
    bot = Bot("123456:synthetic_token_for_tests", session=telegram)
    message = Message(
        message_id=1,
        date=now(),
        chat=Chat(id=100, type="private"),
        text="/start login",
        from_user=TelegramUser(id=100, is_bot=False, first_name="Alex"),
    )
    await menu_dispatcher.feed_update(bot, Update(update_id=2, message=message))
    login.assert_called_once_with(100, "Alex")
    assert "#token=synthetic" in telegram.call_args.args[1].text


@pytest.mark.parametrize("command", ["/start", "/menu"])
async def test_welcome_has_visible_buttons(menu_dispatcher, command):
    telegram = AsyncMock(return_value=SimpleNamespace())
    bot = Bot("123456:synthetic_token_for_tests", session=telegram)
    message = Message(
        message_id=1,
        date=now(),
        chat=Chat(id=100, type="private"),
        text=command,
        from_user=TelegramUser(id=100, is_bot=False, first_name="Alex"),
    )
    await menu_dispatcher.feed_update(bot, Update(update_id=3, message=message))
    buttons = telegram.call_args.args[1].reply_markup.inline_keyboard
    assert [[button.text for button in row] for row in buttons] == [
        [ADD_TASK, "⏰ Due soon"],
        [HELP, TASKS],
        [LOGOUT, "🗂 Projects"],
    ]
    bottom = next(
        markup
        for call in telegram.await_args_list
        if isinstance((markup := getattr(call.args[1], "reply_markup", None)), ReplyKeyboardMarkup)
    )
    assert bottom == main_keyboard()


@pytest.mark.parametrize("action", ["menu:tasks", "menu:help", "menu:logout_prompt"])
async def test_inline_menu_actions_use_clicking_owner(monkeypatch, action):
    tasks = Mock(return_value=([], False))
    revoke = Mock()
    monkeypatch.setattr("app.bot.telegram_tasks", tasks)
    monkeypatch.setattr("app.bot.logout_telegram_user", revoke)
    callback = query(action)
    await menu_callback(callback)
    callback.answer.assert_awaited_once()
    assert callback.message.answer.await_count == (2 if action == "menu:help" else 1)
    assert not revoke.called  # The logout menu must ask for confirmation first.
    if action == "menu:tasks":
        tasks.assert_called_once_with(100)
    if action == "menu:logout_prompt":
        assert "Log out of all" in callback.message.answer.call_args.args[0]


async def test_logout_button_replaces_confirmation(monkeypatch):
    logout = Mock(return_value=2)
    monkeypatch.setattr("app.bot.logout_telegram_user", logout)
    callback = query("menu:logout")
    await logout_callback(callback)
    logout.assert_called_once_with(100)
    assert "Logged out of Telegram and 2" in callback.message.answer.call_args.args[0]
    assert (
        callback.message.answer.call_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
        == "auth:login"
    )


async def test_logout_telegram_only_button(monkeypatch):
    logout = Mock(return_value=0)
    monkeypatch.setattr("app.bot.logout_telegram_user", logout)
    callback = query("menu:logout_telegram")
    await logout_callback(callback)
    logout.assert_called_once_with(100, False)
    assert "Logged out of Telegram only" in callback.message.answer.call_args.args[0]
    assert (
        callback.message.answer.call_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
        == "auth:login"
    )


def test_keyboard_labels_are_short_and_distinct():
    rows = inline_menu().inline_keyboard
    labels = [button.text for row in rows for button in row]
    assert len(set(labels)) == 6
    assert [[button.text for button in row] for row in rows] == [
        [ADD_TASK, "⏰ Due soon"],
        [HELP, TASKS],
        [LOGOUT, "🗂 Projects"],
    ]
    assert LOGIN not in labels
    for markup in (inline_menu(), tasks_keyboard(0, False)):
        buttons = [button for row in markup.inline_keyboard for button in row]
        assert any(button.callback_data == "menu:newtask" for button in buttons)
        assert all(button.callback_data != "menu:login" for button in buttons)
    tasks_buttons = [button for row in tasks_keyboard(0, False).inline_keyboard for button in row]
    assert any(button.callback_data == "menu:main" for button in tasks_buttons)


async def test_menu_button_routes_without_creating_task(monkeypatch, menu_dispatcher):
    create = Mock()
    monkeypatch.setattr("app.bot.create_text_task", create)
    telegram = AsyncMock(return_value=SimpleNamespace())
    bot = Bot("123456:synthetic_token_for_tests", session=telegram)
    message = Message(
        message_id=1,
        date=now(),
        chat=Chat(id=100, type="private"),
        text=MENU,
        from_user=TelegramUser(id=100, is_bot=False, first_name="Alex"),
    )
    await menu_dispatcher.feed_update(bot, Update(update_id=10, message=message))
    assert not create.called
    assert telegram.await_count >= 1


async def test_menu_main_callback(menu_dispatcher):
    telegram = AsyncMock(return_value=SimpleNamespace())
    bot = Bot("123456:synthetic_token_for_tests", session=telegram)
    user = TelegramUser(id=100, is_bot=False, first_name="Alex")
    message = Message(
        message_id=2,
        date=now(),
        chat=Chat(id=100, type="private"),
        from_user=user,
    )
    callback = CallbackQuery(
        id="123",
        from_user=user,
        chat_instance="ci",
        data="menu:main",
        message=message,
    )
    await menu_dispatcher.feed_update(bot, Update(update_id=11, callback_query=callback))
    messages = [
        call.args[1]
        for call in telegram.await_args_list
        if type(call.args[1]).__name__ == "SendMessage"
    ]
    assert messages[0].reply_markup == main_keyboard()
    assert messages[-1].reply_markup == inline_menu()


def test_logout_invalidates_unspent_login_link(client, login):
    login()
    token = dashboard_token(100, "Alex").split("#token=")[1]
    logout_telegram_user(100)
    assert client.get("/api/me").status_code == 401
    assert client.post("/api/auth/exchange", json={"token": token}).status_code == 401
    with pytest.raises(PermissionError):
        dashboard_token(100, "Alex")
    login()
    fresh = dashboard_token(100, "Alex").split("#token=")[1]
    assert client.post("/api/auth/exchange", json={"token": fresh}).status_code == 200


async def feed_login_text(dispatcher, session, text, telegram_id=500):
    bot = Bot("123456:synthetic_token_for_tests", session=session)
    message = Message(
        message_id=10,
        date=now(),
        chat=Chat(id=telegram_id, type="private"),
        text=text,
        from_user=TelegramUser(id=telegram_id, is_bot=False, first_name="Tester"),
    )
    await dispatcher.feed_update(bot, Update(update_id=10, message=message))


@pytest.mark.parametrize(
    "text",
    [
        "/start",
        "/tasks",
        "/newtask",
        "/dashboard",
        "/logout",
        "/help",
        ADD_TASK,
        TASKS,
        "Create a task",
    ],
)
async def test_logged_out_bot_does_not_expose_task_actions(menu_dispatcher, text):
    session = AsyncMock(return_value=SimpleNamespace())
    await feed_login_text(menu_dispatcher, session, text)
    last = session.call_args.args[1]
    assert last.reply_markup.inline_keyboard[0][0].callback_data == "auth:login"
    assert sum(len(row) for row in last.reply_markup.inline_keyboard) == 1
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 0
        assert db.get(TelegramSession, 500) is None


@pytest.mark.parametrize(
    "data",
    [
        "menu:tasks",
        "menu:newtask",
        "tasknew:personal",
        "tasknew:cancel",
        "menu:help",
        "menu:logout_prompt",
        "menu:logout",
        "menu:logout_telegram",
        "tasks:0",
        "retry:00000000-0000-0000-0000-000000000000",
    ],
)
async def test_old_buttons_are_blocked_before_login(menu_dispatcher, data):
    session = AsyncMock(return_value=SimpleNamespace())
    bot = Bot("123456:synthetic_token_for_tests", session=session)
    message = Message(message_id=1, date=now(), chat=Chat(id=500, type="private"), text="Old menu")
    callback = CallbackQuery(
        id="test",
        chat_instance="test",
        data=data,
        message=message,
        from_user=TelegramUser(id=500, is_bot=False, first_name="Tester"),
    )
    await menu_dispatcher.feed_update(bot, Update(update_id=11, callback_query=callback))
    assert (
        session.call_args.args[1].reply_markup.inline_keyboard[0][0].callback_data == "auth:login"
    )


@pytest.mark.parametrize(
    "email,password", [("test1@hire.me", "hireme1"), ("test2@hire.me", "hireme2")]
)
async def test_telegram_email_password_flow_and_task_creation(menu_dispatcher, email, password):
    session = AsyncMock(return_value=SimpleNamespace())
    for text in ("/login", email, password):
        await feed_login_text(menu_dispatcher, session, text)
    with SessionLocal() as db:
        binding = db.get(TelegramSession, 500)
        assert binding is not None
        owner = db.get(User, binding.user_id)
        assert owner.email == email
        assert db.scalar(select(func.count()).select_from(Task)) == 0
    methods = [call.args[1] for call in session.await_args_list]
    assert any(type(method).__name__ == "DeleteMessage" for method in methods)
    assert all(password not in getattr(method, "text", "") for method in methods)
    assert (
        await menu_dispatcher.storage.get_data(StorageKey(bot_id=123456, chat_id=500, user_id=500))
        == {}
    )
    await feed_login_text(menu_dispatcher, session, "Created after login")
    with SessionLocal() as db:
        assert db.scalar(select(Task)).user_id == binding.user_id


async def test_incorrect_telegram_password_never_unlocks_bot(menu_dispatcher):
    session = AsyncMock(return_value=SimpleNamespace())
    for text in ("/login", "test1@hire.me", "incorrect"):
        await feed_login_text(menu_dispatcher, session, text)
    assert "incorrect" in session.call_args.args[1].text
    with SessionLocal() as db:
        assert db.get(TelegramSession, 500) is None
        assert db.scalar(select(func.count()).select_from(Task)) == 0


@pytest.mark.parametrize("pending", ["none", "email", "password"])
async def test_voice_is_blocked_before_login_and_during_credentials(
    menu_dispatcher, monkeypatch, pending
):
    session = AsyncMock(return_value=SimpleNamespace())
    enqueue = Mock()
    monkeypatch.setattr("app.project_bot.messages.queue_voice_input", enqueue)
    if pending != "none":
        await feed_login_text(menu_dispatcher, session, "/login")
    if pending == "password":
        await feed_login_text(menu_dispatcher, session, "test1@hire.me")
    bot = Bot("123456:synthetic_token_for_tests", session=session)
    message = Message(
        message_id=20,
        date=now(),
        chat=Chat(id=500, type="private"),
        from_user=TelegramUser(id=500, is_bot=False, first_name="Tester"),
        voice=Voice(file_id="test", file_unique_id="test", duration=2),
    )
    await menu_dispatcher.feed_update(bot, Update(update_id=20, message=message))
    assert not enqueue.called


async def test_telegram_logout_relocks_all_task_actions(menu_dispatcher):
    session = AsyncMock(return_value=SimpleNamespace())
    for text in ("/login", "test1@hire.me", "hireme1"):
        await feed_login_text(menu_dispatcher, session, text)
    bot = Bot("123456:synthetic_token_for_tests", session=session)
    message = Message(message_id=21, date=now(), chat=Chat(id=500, type="private"), text="Logout")
    callback = CallbackQuery(
        id="logout",
        chat_instance="test",
        data="menu:logout",
        message=message,
        from_user=TelegramUser(id=500, is_bot=False, first_name="Tester"),
    )
    await menu_dispatcher.feed_update(bot, Update(update_id=21, callback_query=callback))
    with SessionLocal() as db:
        assert db.get(TelegramSession, 500) is None
    await feed_login_text(menu_dispatcher, session, "/tasks")
    assert (
        session.call_args.args[1].reply_markup.inline_keyboard[0][0].callback_data == "auth:login"
    )


async def project_feed(
    dispatcher, session, user_id, text=None, data=None, identifier=100, media=None
):
    bot = Bot("123456:synthetic_token_for_tests", session=session)
    sender = TelegramUser(id=user_id, is_bot=False, first_name="Tester")
    message = Message(
        message_id=identifier,
        date=now(),
        chat=Chat(id=user_id, type="private"),
        text=None if media else text or "Menu",
        from_user=sender,
        **(media or {}),
    )
    if data:
        callback = CallbackQuery(
            id=str(identifier), chat_instance="test", data=data, message=message, from_user=sender
        )
        await dispatcher.feed_update(bot, Update(update_id=identifier, callback_query=callback))
    else:
        await dispatcher.feed_update(bot, Update(update_id=identifier, message=message))


async def approve_recording(dispatcher, session, user_id, transcript):
    """Supply the worker result to the transport tests' in-memory FSM."""
    from app.models import VoiceJob
    from app.voice_input import VOICE_ANSWER, normalize_answer

    key = StorageKey(bot_id=123456, chat_id=user_id, user_id=user_id)
    data = await dispatcher.storage.get_data(key)
    pending = data[VOICE_ANSWER]
    pending["text"] = normalize_answer(pending["state"], transcript)
    pending["revision"] = 0
    await dispatcher.storage.set_data(key, data)
    with SessionLocal.begin() as db:
        job = db.scalar(select(VoiceJob).where(VoiceJob.input_token == pending["token"]))
        job.status = "completed"
    await project_feed(
        dispatcher, session, user_id, data=f"voiceinput:{pending['token']}:approve:0"
    )


@pytest.mark.parametrize("action,field", [("taskedittitle", "title"), ("taskedittext", "text")])
@pytest.mark.parametrize("media_type", ["voice", "audio"])
async def test_edit_task_queues_recordings_for_the_worker(
    monkeypatch, menu_dispatcher, action, field, media_type
):
    from app.models import VoiceJob

    target = create_text_task(100, "Alex", 100, 700, "Original description")
    convert = Mock()
    monkeypatch.setattr("app.transcription.transcribe_audio", convert)
    session = AsyncMock(return_value=SimpleNamespace())
    await project_feed(menu_dispatcher, session, 100, data=f"{action}:{target}")
    assert "text or a voice note" in session.call_args.args[1].text
    media_class = Voice if media_type == "voice" else Audio
    await project_feed(
        menu_dispatcher,
        session,
        100,
        identifier=702,
        media={media_type: media_class(file_id="edit", file_unique_id="edit", duration=5)},
    )
    # The bot only saves the job and acknowledges; Whisper never runs in its event loop.
    assert not convert.called
    assert "Got it" in session.call_args.args[1].text
    key = StorageKey(bot_id=123456, chat_id=100, user_id=100)
    assert await menu_dispatcher.storage.get_state(key) == f"WorkFlow:edit_{field}"
    with SessionLocal() as db:
        job = db.scalar(select(VoiceJob))
        assert job.kind == "input"
        assert job.file_id == "edit" and job.message_id == 702 and job.status == "queued"
        task = db.get(Task, target)
        assert task.title == task.text == "Original description"
        assert db.scalar(select(func.count()).select_from(Task)) == 1


@pytest.mark.parametrize("action", ["taskedittitle", "taskedittext"])
@pytest.mark.parametrize("failure", ["duration", "size"])
async def test_oversized_voice_edit_is_rejected_and_allows_text_retry(
    menu_dispatcher, action, failure
):
    from app.models import VoiceJob

    target = create_text_task(100, "Alex", 100, 700, "Original description")
    session = AsyncMock(return_value=SimpleNamespace())
    await project_feed(menu_dispatcher, session, 100, data=f"{action}:{target}")
    await project_feed(
        menu_dispatcher,
        session,
        100,
        identifier=702,
        media={
            "voice": Voice(
                file_id="edit",
                file_unique_id="edit",
                duration=301 if failure == "duration" else 5,
                file_size=21 * 1024 * 1024 if failure == "size" else 1000,
            )
        },
    )
    assert "5 minutes and 20 MB" in session.call_args.args[1].text
    key = StorageKey(bot_id=123456, chat_id=100, user_id=100)
    expected_state = "WorkFlow:edit_title" if action == "taskedittitle" else "WorkFlow:edit_text"
    assert await menu_dispatcher.storage.get_state(key) == expected_state
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(VoiceJob)) == 0
    await project_feed(menu_dispatcher, session, 100, text="Typed replacement", identifier=703)
    assert await menu_dispatcher.storage.get_state(key) is None
    with SessionLocal() as db:
        task = db.get(Task, target)
        assert (task.title if action == "taskedittitle" else task.text) == "Typed replacement"


async def test_voice_edit_of_unmanaged_task_is_refused_immediately(menu_dispatcher):
    from app.models import VoiceJob
    from app.workspace import add_member, create_project, create_task

    with SessionLocal() as db:
        manager = db.scalar(select(User).where(User.telegram_id == 100))
    project = create_project(manager, "Managed")
    add_member(manager, project["id"], "test2@hire.me")
    task = create_task(manager, "Spec", project_id=project["id"])
    session = AsyncMock(return_value=SimpleNamespace())
    key = StorageKey(bot_id=123456, chat_id=200, user_id=200)
    await menu_dispatcher.storage.set_state(key, "WorkFlow:edit_title")
    await menu_dispatcher.storage.set_data(key, {"edit_task": str(task["id"])})
    await project_feed(
        menu_dispatcher,
        session,
        200,
        identifier=702,
        media={"voice": Voice(file_id="edit", file_unique_id="edit", duration=5)},
    )
    assert "project manager" in session.call_args.args[1].text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(VoiceJob)) == 0


@pytest.mark.parametrize("entry", [ADD_TASK, "/newtask", "menu:newtask"])
async def test_new_task_entrypoints_reset_old_conversation_and_create_personal_task(
    menu_dispatcher, entry
):
    from app.auth import telegram_user
    from app.workspace import create_project

    owner = telegram_user(100)
    project = create_project(owner, "Launch project")
    session = AsyncMock(return_value=SimpleNamespace())
    await project_feed(menu_dispatcher, session, 100, text="/newproject")
    await project_feed(
        menu_dispatcher,
        session,
        100,
        data=entry if entry.startswith("menu:") else None,
        text=None if entry.startswith("menu:") else entry,
    )
    buttons = session.call_args.args[1].reply_markup.inline_keyboard
    assert {button.callback_data for row in buttons for button in row} == {
        "tasknew:personal",
        "tasknew:cancel",
        f"projectadd:{project['id']}",
    }
    await project_feed(menu_dispatcher, session, 100, text="Wait until a project is selected")
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 0
    await project_feed(menu_dispatcher, session, 100, data="tasknew:personal")
    await project_feed(menu_dispatcher, session, 100, text="Personal headline", identifier=699)
    await project_feed(
        menu_dispatcher, session, 100, text="Write the launch checklist", identifier=701
    )
    assert "Choose a deadline date" in session.call_args.args[1].text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 0
    token = latest_calendar_token(session)
    await project_feed(menu_dispatcher, session, 100, data=f"dl:{token}:none")
    with SessionLocal() as db:
        tasks = list(db.scalars(select(Task)))
        assert len(tasks) == 1
        assert tasks[0].user_id == owner.id and tasks[0].project_id is None
        assert tasks[0].text == "Write the launch checklist"
        assert tasks[0].title == "Personal headline"


async def test_add_task_accepts_personal_voice_message(menu_dispatcher):
    from app.models import VoiceJob

    session = AsyncMock(return_value=SimpleNamespace())
    await project_feed(menu_dispatcher, session, 100, data="menu:newtask")
    await project_feed(menu_dispatcher, session, 100, data="tasknew:personal")
    await project_feed(menu_dispatcher, session, 100, text="Personal headline", identifier=699)
    bot = Bot("123456:synthetic_token_for_tests", session=session)
    message = Message(
        message_id=702,
        date=now(),
        chat=Chat(id=100, type="private"),
        from_user=TelegramUser(id=100, is_bot=False, first_name="Tester"),
        voice=Voice(file_id="personal-voice", file_unique_id="personal-unique", duration=5),
    )
    await menu_dispatcher.feed_update(bot, Update(update_id=702, message=message))
    assert "transcribing your task description" in session.call_args.args[1].text
    await approve_recording(menu_dispatcher, session, 100, "Spoken personal description")
    assert "Choose a deadline date" in session.call_args.args[1].text
    token = latest_calendar_token(session)
    await project_feed(menu_dispatcher, session, 100, data=f"dl:{token}:none")
    with SessionLocal() as db:
        job = db.scalar(select(VoiceJob))
        assert job.message_id == 702 and job.project_id is None
        assert job.kind == "input" and job.status == "completed"
        task = db.scalar(select(Task))
        assert task.title == "Personal headline" and task.text == "Spoken personal description"
        assert task.source == "voice" and task.due_at is None


async def test_voice_description_is_saved_before_acknowledging(menu_dispatcher):
    from aiogram.methods import SendMessage

    from app.models import VoiceJob

    session = AsyncMock(return_value=SimpleNamespace())
    await project_feed(menu_dispatcher, session, 100, data="menu:newtask")
    await project_feed(menu_dispatcher, session, 100, data="tasknew:personal")
    await project_feed(menu_dispatcher, session, 100, text="Personal headline", identifier=699)
    saved_when_acknowledged = []

    async def transport(bot, method, *args, **kwargs):
        if isinstance(method, SendMessage) and "transcribing your task description" in method.text:
            with SessionLocal() as db:
                saved_when_acknowledged.append(db.scalar(select(VoiceJob.message_id)))
            raise RuntimeError("Telegram unavailable")
        return SimpleNamespace()

    session.side_effect = transport
    await project_feed(
        menu_dispatcher,
        session,
        100,
        identifier=702,
        media={"voice": Voice(file_id="ordered", file_unique_id="ordered", duration=5)},
    )
    # The job was durable before the reply was attempted, so a failed reply loses nothing.
    assert saved_when_acknowledged == [702]
    with SessionLocal() as db:
        assert db.scalar(select(VoiceJob)).status == "queued"


async def test_add_task_voice_title_is_queued_with_immediate_acknowledgment(
    monkeypatch, menu_dispatcher
):
    from app.models import VoiceJob

    convert = Mock()
    monkeypatch.setattr("app.transcription.transcribe_audio", convert)
    session = AsyncMock(return_value=SimpleNamespace())
    await project_feed(menu_dispatcher, session, 100, data="menu:newtask")
    await project_feed(menu_dispatcher, session, 100, data="tasknew:personal")
    bot = Bot("123456:synthetic_token_for_tests", session=session)
    message = Message(
        message_id=710,
        date=now(),
        chat=Chat(id=100, type="private"),
        from_user=TelegramUser(id=100, is_bot=False, first_name="Tester"),
        voice=Voice(file_id="voice-as-title", file_unique_id="voice-as-title-unique", duration=4),
    )
    await menu_dispatcher.feed_update(bot, Update(update_id=710, message=message))
    assert not convert.called
    assert "Got it" in session.call_args.args[1].text
    key = StorageKey(bot_id=123456, chat_id=100, user_id=100)
    # The title is reviewed before the draft advances (see test_voice_review).
    assert await menu_dispatcher.storage.get_state(key) == "WorkFlow:task_title"
    assert (await menu_dispatcher.storage.get_data(key))["voice_answer"]["message_id"] == 710
    with SessionLocal() as db:
        job = db.scalar(select(VoiceJob))
        assert job.kind == "input" and job.message_id == 710 and job.status == "queued"
        assert db.scalar(select(func.count()).select_from(Task)) == 0


async def test_typed_title_still_works_while_voice_title_is_pending(menu_dispatcher):
    session = AsyncMock(return_value=SimpleNamespace())
    await project_feed(menu_dispatcher, session, 100, data="menu:newtask")
    await project_feed(menu_dispatcher, session, 100, data="tasknew:personal")
    await project_feed(
        menu_dispatcher,
        session,
        100,
        identifier=711,
        media={"voice": Voice(file_id="title", file_unique_id="title", duration=3)},
    )
    await project_feed(menu_dispatcher, session, 100, text="Typed headline", identifier=712)
    key = StorageKey(bot_id=123456, chat_id=100, user_id=100)
    assert await menu_dispatcher.storage.get_state(key) == "WorkFlow:task_content"
    assert (await menu_dispatcher.storage.get_data(key)).get("voice_title_message") is None
    await project_feed(
        menu_dispatcher, session, 100, text="Detailed description for the task", identifier=713
    )
    await project_feed(menu_dispatcher, session, 100, text="2028-02-29 18:30", identifier=714)
    with SessionLocal() as db:
        tasks = list(db.scalars(select(Task)))
        assert len(tasks) == 1
        assert tasks[0].title == "Typed headline"
        assert tasks[0].text == "Detailed description for the task"


@pytest.mark.parametrize("invalid", [" \n\t ", "x" * 121])
async def test_task_title_step_rejects_invalid_headlines_without_creating_tasks(
    menu_dispatcher, invalid
):
    from app.models import VoiceJob

    session = AsyncMock(return_value=SimpleNamespace())
    await project_feed(menu_dispatcher, session, 100, data="menu:newtask")
    await project_feed(menu_dispatcher, session, 100, data="tasknew:personal")
    await project_feed(menu_dispatcher, session, 100, text=invalid)
    assert "1–120 characters" in session.call_args.args[1].text
    key = StorageKey(bot_id=123456, chat_id=100, user_id=100)
    assert await menu_dispatcher.storage.get_state(key) == "WorkFlow:task_title"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 0
        assert db.scalar(select(func.count()).select_from(VoiceJob)) == 0
    await project_feed(menu_dispatcher, session, 100, text="Short headline", identifier=705)
    assert "Step 2 · Task description" in session.call_args.args[1].text
    await project_feed(menu_dispatcher, session, 100, text="/cancel")
    assert await menu_dispatcher.storage.get_state(key) is None
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 0


@pytest.mark.parametrize("media_type,media_class", [("voice", Voice), ("audio", Audio)])
@pytest.mark.parametrize("duration,size", [(5, 1000), (301, 1000), (5, 21 * 1024 * 1024)])
async def test_comment_accepts_voice_and_keeps_target(
    menu_dispatcher, duration, size, media_type, media_class
):
    from app.models import VoiceJob

    target = create_text_task(100, "Alex", 100, 700, "Task with a voice comment")
    session = AsyncMock(return_value=SimpleNamespace())
    await project_feed(menu_dispatcher, session, 100, data=f"taskcomment:{target}")
    assert "voice note" in session.call_args.args[1].text
    bot = Bot("123456:synthetic_token_for_tests", session=session)
    message = Message(
        message_id=703,
        date=now(),
        chat=Chat(id=100, type="private"),
        from_user=TelegramUser(id=100, is_bot=False, first_name="Tester"),
        **{
            media_type: media_class(
                file_id="comment-voice",
                file_unique_id="comment-unique",
                duration=duration,
                file_size=size,
            )
        },
    )
    await menu_dispatcher.feed_update(bot, Update(update_id=703, message=message))
    accepted = duration <= 300 and size <= 20 * 1024 * 1024
    with SessionLocal() as db:
        job = db.scalar(select(VoiceJob))
        if accepted:
            assert job.kind == "input" and job.message_id == 703
            assert job.status == "queued"
            assert "transcribing your comment" in session.call_args.args[1].text
        else:
            assert job is None
            assert "5 minutes and 20 MB" in session.call_args.args[1].text
        assert db.scalar(select(func.count()).select_from(Task)) == 1
    assert (
        await menu_dispatcher.storage.get_state(StorageKey(bot_id=123456, chat_id=100, user_id=100))
        == "WorkFlow:comment"
    )


@pytest.mark.parametrize("cancel", ["tasknew:cancel", "/cancel"])
async def test_task_project_chooser_can_be_cancelled(menu_dispatcher, cancel):
    session = AsyncMock(return_value=SimpleNamespace())
    await project_feed(menu_dispatcher, session, 100, data="menu:newtask")
    await project_feed(
        menu_dispatcher,
        session,
        100,
        data=cancel if cancel.startswith("tasknew:") else None,
        text=cancel if cancel.startswith("/") else None,
    )
    assert "cancelled" in session.call_args.args[1].text.lower()
    assert (
        await menu_dispatcher.storage.get_state(StorageKey(bot_id=123456, chat_id=100, user_id=100))
        is None
    )
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 0


async def test_telegram_project_assignment_status_and_comment_flow(menu_dispatcher):
    from datetime import timedelta

    from app.models import Project, TaskComment

    session = AsyncMock(return_value=SimpleNamespace())
    await project_feed(menu_dispatcher, session, 100, text="/newproject")
    await project_feed(menu_dispatcher, session, 100, text="Telegram Launch", identifier=101)
    with SessionLocal() as db:
        project = db.scalar(select(Project))
        developer = db.scalar(select(User).where(User.telegram_id == 200))
    await project_feed(menu_dispatcher, session, 100, data=f"projectmember:{project.id}")
    await project_feed(menu_dispatcher, session, 100, text=developer.email, identifier=102)
    await project_feed(menu_dispatcher, session, 100, data="menu:newtask")
    assert any(
        button.callback_data == f"projectadd:{project.id}"
        for row in session.call_args.args[1].reply_markup.inline_keyboard
        for button in row
    )
    await project_feed(menu_dispatcher, session, 100, data=f"projectadd:{project.id}")
    await project_feed(menu_dispatcher, session, 100, text="Project headline", identifier=499)
    await project_feed(menu_dispatcher, session, 100, text="Ship the project", identifier=103)
    await project_feed(menu_dispatcher, session, 100, data=f"assignee:{developer.id}")
    deadline = (now() + timedelta(days=2)).date().isoformat()
    await project_feed(menu_dispatcher, session, 100, text=deadline, identifier=104)
    with SessionLocal() as db:
        task = db.scalar(select(Task))
        assert task is not None and task.project_id == project.id
        assert task.assignee_id == developer.id and task.due_at is not None
    await project_feed(menu_dispatcher, session, 200, data=f"taskstatus:{task.id}:completed")
    await project_feed(menu_dispatcher, session, 200, data=f"taskcomment:{task.id}")
    await project_feed(menu_dispatcher, session, 200, text="Implemented and tested", identifier=105)
    with SessionLocal() as db:
        assert db.get(Task, task.id).status == "completed"
        assert db.scalar(select(TaskComment)).text == "Implemented and tested"
    for call in session.await_args_list:
        markup = getattr(call.args[1], "reply_markup", None)
        for row in getattr(markup, "inline_keyboard", []):
            for button in row:
                assert len((button.callback_data or "").encode()) <= 64


async def test_developer_cannot_use_stale_manager_callbacks(menu_dispatcher):
    from app import workspace
    from app.models import Project

    with SessionLocal() as db:
        pm = db.scalar(select(User).where(User.telegram_id == 100))
    project = workspace.create_project(pm, "Restricted")
    workspace.add_member(pm, project["id"], "test2@hire.me")
    session = AsyncMock(return_value=SimpleNamespace())
    await project_feed(menu_dispatcher, session, 200, data=f"projectadd:{project['id']}")
    assert "Only the project manager" in session.call_args.args[1].text
    await project_feed(menu_dispatcher, session, 200, text="/newproject")
    assert "Only project managers" in session.call_args.args[1].text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Project)) == 1


async def test_telegram_deadline_and_due_view_and_cancel(menu_dispatcher):
    from datetime import timedelta

    from app import workspace

    with SessionLocal() as db:
        pm = db.scalar(select(User).where(User.telegram_id == 100))
    task = workspace.create_task(pm, "A deadline task", due_at=now() + timedelta(days=1))
    session = AsyncMock(return_value=SimpleNamespace())
    await project_feed(menu_dispatcher, session, 100, text="/due")
    assert "A deadline task" in session.call_args.args[1].text
    await project_feed(menu_dispatcher, session, 100, data=f"taskdue:{task['id']}")
    await project_feed(menu_dispatcher, session, 100, text="wrong-date")
    assert "YYYY-MM-DD" in session.call_args.args[1].text
    await project_feed(menu_dispatcher, session, 100, text="-")
    with SessionLocal() as db:
        assert db.get(Task, task["id"]).due_at is None
    await project_feed(menu_dispatcher, session, 100, text="/newproject")
    await project_feed(menu_dispatcher, session, 100, text="/cancel")
    assert "Cancelled" in session.call_args.args[1].text


def latest_calendar_token(session):
    for call in reversed(session.await_args_list):
        markup = getattr(call.args[1], "reply_markup", None)
        for row in getattr(markup, "inline_keyboard", []):
            for button in row:
                if (button.callback_data or "").startswith("dl:"):
                    return button.callback_data.split(":")[1]
    raise AssertionError("No calendar was displayed")


@pytest.mark.parametrize("description", ["text", "voice", "skip"])
@pytest.mark.parametrize("completion", ["save", "none", "cancel"])
async def test_personal_task_waits_for_deadline_before_saving(
    menu_dispatcher, description, completion
):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.models import VoiceJob

    # Developers can create personal tasks without choosing a project or an assignee.
    session = AsyncMock(return_value=SimpleNamespace())
    await project_feed(menu_dispatcher, session, 200, text="/newtask", identifier=710)
    await project_feed(menu_dispatcher, session, 200, data="tasknew:personal", identifier=711)
    await project_feed(menu_dispatcher, session, 200, text="Personal headline", identifier=712)
    await project_feed(
        menu_dispatcher,
        session,
        200,
        text="Personal details" if description == "text" else None,
        data="tasknew:nodescription" if description == "skip" else None,
        media={"voice": Voice(file_id="personal", file_unique_id="personal", duration=5)}
        if description == "voice"
        else None,
        identifier=713,
    )
    key = StorageKey(bot_id=123456, chat_id=200, user_id=200)
    if description == "voice":
        await approve_recording(menu_dispatcher, session, 200, "Personal details")
    assert await menu_dispatcher.storage.get_state(key) == "WorkFlow:task_due"
    assert "Choose a deadline date" in session.call_args.args[1].text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 0
        assert db.scalar(select(func.count()).select_from(VoiceJob)) == (
            1 if description == "voice" else 0
        )

    token = latest_calendar_token(session)
    actions = ("day:2028-02-29", "time:1080", "save") if completion == "save" else (completion,)
    bot = Bot("123456:synthetic_token_for_tests", session=session)
    for index, action in enumerate(actions):
        # Telegram callbacks carry a bot-authored calendar and a separate clicking person.
        query = CallbackQuery(
            id=str(index),
            chat_instance="test",
            data=f"dl:{token}:{action}",
            from_user=TelegramUser(id=200, is_bot=False, first_name="Developer"),
            message=Message(
                message_id=714,
                date=now(),
                chat=Chat(id=200, type="private"),
                text="Calendar",
                from_user=TelegramUser(id=123456, is_bot=True, first_name="Bot"),
            ),
        )
        await menu_dispatcher.feed_update(bot, Update(update_id=715 + index, callback_query=query))

    assert await menu_dispatcher.storage.get_state(key) is None
    # Completed or cancelled calendars cannot create another task or voice job.
    await project_feed(menu_dispatcher, session, 200, data=f"dl:{token}:none", identifier=720)
    deadline = (
        datetime(2028, 2, 29, 18, 0, tzinfo=ZoneInfo("Asia/Yerevan"))
        if completion == "save"
        else None
    )
    with SessionLocal() as db:
        tasks = list(db.scalars(select(Task)))
        jobs = list(db.scalars(select(VoiceJob)))
        if completion == "cancel":
            assert tasks == []
            assert len(jobs) == (1 if description == "voice" else 0)
        else:
            assert len(tasks) == 1
            assert len(jobs) == (1 if description == "voice" else 0)
            saved = tasks[0]
            owner = db.scalar(select(User).where(User.telegram_id == 200))
            assert saved.user_id == saved.assignee_id == owner.id
            assert saved.project_id is None and saved.due_at == deadline
            assert saved.chat_id == 200 and saved.message_id == 713
            if description == "voice":
                assert saved.source == "voice" and jobs[0].file_id == "personal"
            else:
                assert saved.title == "Personal headline"
                assert saved.text == (
                    "Personal headline" if description == "skip" else "Personal details"
                )


async def test_telegram_calendar_creates_task_with_date_and_time_buttons(menu_dispatcher):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app import workspace

    with SessionLocal() as db:
        pm = db.scalar(select(User).where(User.telegram_id == 100))
        developer = db.scalar(select(User).where(User.telegram_id == 200))
    project = workspace.create_project(pm, "Calendar project")
    workspace.add_member(pm, project["id"], developer.email)
    session = AsyncMock(return_value=SimpleNamespace())
    await project_feed(menu_dispatcher, session, 100, data=f"projectadd:{project['id']}")
    await project_feed(menu_dispatcher, session, 100, text="Project headline", identifier=499)
    await project_feed(menu_dispatcher, session, 100, text="Task from calendar", identifier=501)
    await project_feed(menu_dispatcher, session, 100, data=f"assignee:{developer.id}")
    token = latest_calendar_token(session)
    assert "Choose a deadline date" in session.call_args.args[1].text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 0
    for action in ("month:2028-02", "day:2028-02-29", "time:1080", "adjust:15", "save"):
        await project_feed(menu_dispatcher, session, 100, data=f"dl:{token}:{action}")
    with SessionLocal() as db:
        task = db.scalar(select(Task))
        assert task is not None and task.text == "Task from calendar"
        assert task.title == "Project headline"
        assert task.assignee_id == developer.id
        assert task.due_at == datetime(2028, 2, 29, 18, 15, tzinfo=ZoneInfo("Asia/Yerevan"))
    # A repeated Save from the old keyboard cannot create a second task.
    await project_feed(menu_dispatcher, session, 100, data=f"dl:{token}:save")
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 1


async def test_calendar_cancel_clear_and_cross_user_buttons(menu_dispatcher):
    from datetime import timedelta

    from app import workspace

    with SessionLocal() as db:
        pm = db.scalar(select(User).where(User.telegram_id == 100))
    original = now() + timedelta(days=3)
    task = workspace.create_task(pm, "Keep my deadline", due_at=original)
    session = AsyncMock(return_value=SimpleNamespace())
    await project_feed(menu_dispatcher, session, 100, data=f"taskdue:{task['id']}")
    token = latest_calendar_token(session)
    await project_feed(menu_dispatcher, session, 200, data=f"dl:{token}:none")
    with SessionLocal() as db:
        assert db.get(Task, task["id"]).due_at == original
    await project_feed(menu_dispatcher, session, 100, data=f"dl:{token}:cancel")
    with SessionLocal() as db:
        assert db.get(Task, task["id"]).due_at == original
    await project_feed(menu_dispatcher, session, 100, data=f"taskdue:{task['id']}")
    token = latest_calendar_token(session)
    await project_feed(menu_dispatcher, session, 100, data=f"dl:{token}:none")
    with SessionLocal() as db:
        assert db.get(Task, task["id"]).due_at is None


async def test_voice_calendar_submission_uses_clicking_person_not_bot(menu_dispatcher):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app import workspace

    with SessionLocal() as db:
        pm = db.scalar(select(User).where(User.telegram_id == 100))
        developer = db.scalar(select(User).where(User.telegram_id == 200))
    project = workspace.create_project(pm, "Voice calendar")
    workspace.add_member(pm, project["id"], developer.email)
    session = AsyncMock(return_value=SimpleNamespace())
    await project_feed(menu_dispatcher, session, 100, data=f"projectadd:{project['id']}")
    await project_feed(menu_dispatcher, session, 100, text="Project headline", identifier=499)
    bot = Bot("123456:synthetic_token_for_tests", session=session)
    person = TelegramUser(id=100, is_bot=False, first_name="Manager")
    voice = Message(
        message_id=600,
        date=now(),
        chat=Chat(id=100, type="private"),
        from_user=person,
        voice=Voice(file_id="recording", file_unique_id="unique", duration=5),
    )
    await menu_dispatcher.feed_update(bot, Update(update_id=600, message=voice))
    await approve_recording(menu_dispatcher, session, 100, "Spoken project description")
    await project_feed(menu_dispatcher, session, 100, data=f"assignee:{developer.id}")
    token = latest_calendar_token(session)
    for action in ("day:2028-02-29", "time:540", "save"):
        sent_message = Message(
            message_id=601,
            date=now(),
            chat=Chat(id=100, type="private"),
            text="Calendar",
            from_user=TelegramUser(id=123456, is_bot=True, first_name="Bot"),
        )
        query = CallbackQuery(
            id=action,
            chat_instance="test",
            data=f"dl:{token}:{action}",
            from_user=person,
            message=sent_message,
        )
        await menu_dispatcher.feed_update(bot, Update(update_id=601, callback_query=query))
    with SessionLocal() as db:
        task = db.scalar(select(Task))
        assert task is not None and task.user_id == pm.id and task.assignee_id == developer.id
        assert task.project_id == project["id"] and task.message_id == 600
        assert task.title == "Project headline" and task.source == "voice"
        assert task.due_at == datetime(2028, 2, 29, 9, 0, tzinfo=ZoneInfo("Asia/Yerevan"))


def test_bot_conversations_and_worker_wizard_share_one_ttl():
    import inspect

    import app.bot
    import app.jobs
    from app.config import CONVERSATION_TTL_SECONDS

    assert app.jobs.WIZARD_TTL_SECONDS == CONVERSATION_TTL_SECONDS
    source = inspect.getsource(app.bot.main)
    assert "state_ttl=CONVERSATION_TTL_SECONDS" in source
    assert "data_ttl=CONVERSATION_TTL_SECONDS" in source
