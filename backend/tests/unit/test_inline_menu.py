"""Transport-free checks for matching menus in the chat and below the typing area."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.filters import CommandObject
from aiogram.types import ReplyKeyboardMarkup

from app.bot import (
    already_logged_in_callback,
    dashboard_command,
    help_command,
    login_callback,
    main_menu_callback,
)
from app.bot_ui import (
    ADD_TASK,
    HELP,
    LOGOUT,
    MENU,
    TASKS,
    inline_menu,
    main_keyboard,
    tasks_keyboard,
)


def test_chat_menu_keeps_the_previous_six_button_layout():
    rows = inline_menu().inline_keyboard
    assert [[button.text for button in row] for row in rows] == [
        [ADD_TASK, "⏰ Due soon"],
        [HELP, TASKS],
        [LOGOUT, "🗂 Projects"],
    ]
    assert [[button.callback_data for button in row] for row in rows] == [
        ["menu:newtask", "menu:due"],
        ["menu:help", "menu:tasks"],
        ["menu:logout_prompt", "menu:projects"],
    ]
    bottom = main_keyboard()
    assert [[button.text for button in row] for row in bottom.keyboard[:-1]] == [
        [button.text for button in row] for row in rows
    ]
    assert [button.text for button in bottom.keyboard[-1]] == [MENU]
    assert bottom.is_persistent and bottom.resize_keyboard


@pytest.mark.parametrize(
    "entry", ["callback", "legacy_login", "help", "start", "menu", "legacy_menu"]
)
async def test_all_menu_entrypoints_show_both_chat_and_bottom_buttons(entry):
    message = SimpleNamespace(
        text=MENU if entry == "legacy_menu" else "/" + entry,
        chat=SimpleNamespace(id=123),
        bot=SimpleNamespace(set_my_commands=AsyncMock()),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(clear=AsyncMock())
    callback = SimpleNamespace(message=message, answer=AsyncMock())
    if entry == "callback":
        await main_menu_callback(callback, state)
    elif entry == "legacy_login":
        await already_logged_in_callback(callback)
    else:
        command = None if entry == "legacy_menu" else CommandObject(command=entry)
        await help_command(message, command, state)
    calls = message.answer.await_args_list
    assert len(calls) == 2
    assert isinstance(calls[0].kwargs["reply_markup"], ReplyKeyboardMarkup)
    assert calls[0].kwargs["reply_markup"] == main_keyboard()
    assert calls[1].kwargs["reply_markup"] == inline_menu()
    if entry != "legacy_login":
        state.clear.assert_awaited_once()


def test_task_lists_keep_an_inline_way_back_to_the_menu():
    buttons = [button for row in tasks_keyboard(1, True).inline_keyboard for button in row]
    assert any(button.text == MENU and button.callback_data == "menu:main" for button in buttons)
    assert all(len(button.callback_data.encode()) <= 64 for button in buttons)


@pytest.mark.parametrize("entry", ["command", "callback"])
async def test_dashboard_links_keep_the_bottom_buttons(monkeypatch, entry):
    monkeypatch.setattr(
        "app.bot.dashboard_token", lambda *args: "http://localhost/#token=synthetic"
    )
    sender = SimpleNamespace(id=123, full_name="Menu Tester")
    message = SimpleNamespace(from_user=sender, answer=AsyncMock())
    if entry == "command":
        await dashboard_command(message)
    else:
        await login_callback(SimpleNamespace(from_user=sender, message=message, answer=AsyncMock()))
    assert message.answer.call_args.kwargs["reply_markup"] == main_keyboard()
