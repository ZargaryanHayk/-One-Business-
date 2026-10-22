from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import EditMessageText

from app.deadline_picker import calendar_view, handle_picker, open_picker

VALID_STATES = {"WorkFlow:task_due", "WorkFlow:edit_due"}


@pytest.fixture
def picker_state():
    return FSMContext(MemoryStorage(), StorageKey(bot_id=123, chat_id=100, user_id=100))


def callback(data):
    return SimpleNamespace(
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock()),
    )


def test_calendar_has_leap_days_weekdays_and_bounded_navigation():
    picker = {"token": "test", "month": "2028-02", "selected": "2028-02-29", "minutes": 1439}
    _, buttons = calendar_view(picker)
    assert [button.text for button in buttons.inline_keyboard[1]] == [
        "Mo",
        "Tu",
        "We",
        "Th",
        "Fr",
        "Sa",
        "Su",
    ]
    days = [button for row in buttons.inline_keyboard[2:-2] for button in row]
    assert any(
        button.callback_data == "dl:test:day:2028-02-29" and "✓" in button.text for button in days
    )
    assert not any(button.callback_data.endswith("2028-02-30") for button in days)
    assert all(
        len(button.callback_data.encode()) <= 64
        for row in buttons.inline_keyboard
        for button in row
    )
    _, first = calendar_view({**picker, "month": "1970-01"})
    _, last = calendar_view({**picker, "month": "2100-12"})
    assert first.inline_keyboard[0][0].callback_data.endswith(":noop")
    assert last.inline_keyboard[0][2].callback_data.endswith(":noop")


async def test_date_time_selection_and_exact_minutes_are_timezone_aware(picker_state):
    await picker_state.set_state("WorkFlow:task_due")
    message = SimpleNamespace(answer=AsyncMock())
    await open_picker(message, picker_state)
    token = (await picker_state.get_data())["deadline_picker"]["token"]
    for action in ("month:2028-02", "day:2028-02-29", "time:1080", "adjust:15", "adjust:1"):
        result = await handle_picker(callback(f"dl:{token}:{action}"), picker_state, VALID_STATES)
        assert result.action == "updated"
    result = await handle_picker(callback(f"dl:{token}:save"), picker_state, VALID_STATES)
    assert result.action == "save"
    assert result.deadline == datetime(2028, 2, 29, 18, 16, tzinfo=ZoneInfo("Asia/Yerevan"))
    assert result.deadline.astimezone(UTC).hour == 14


async def test_existing_deadline_initializes_calendar_in_local_timezone(picker_state):
    await picker_state.set_state("WorkFlow:edit_due")
    message = SimpleNamespace(answer=AsyncMock())
    original = datetime(2026, 10, 22, 20, 30, tzinfo=UTC)
    await open_picker(message, picker_state, original)
    picker = (await picker_state.get_data())["deadline_picker"]
    assert picker["selected"] == "2026-10-23"
    assert picker["minutes"] == 30
    result = await handle_picker(callback(f"dl:{picker['token']}:save"), picker_state, VALID_STATES)
    assert result.deadline == original


@pytest.mark.parametrize("action", ["none", "cancel"])
async def test_clear_and_cancel_are_explicit_results(picker_state, action):
    await picker_state.set_state("WorkFlow:edit_due")
    await open_picker(SimpleNamespace(answer=AsyncMock()), picker_state)
    token = (await picker_state.get_data())["deadline_picker"]["token"]
    result = await handle_picker(callback(f"dl:{token}:{action}"), picker_state, VALID_STATES)
    assert result.action == action and result.deadline is None


@pytest.mark.parametrize(
    "action",
    ["day:2026-02-30", "month:2101-01", "day:1969-12-31", "time:1440", "adjust:999", "unknown"],
)
async def test_invalid_calendar_payloads_do_not_apply_a_deadline(picker_state, action):
    await picker_state.set_state("WorkFlow:edit_due")
    await open_picker(
        SimpleNamespace(answer=AsyncMock()), picker_state, datetime(2026, 10, 21, tzinfo=UTC)
    )
    data = await picker_state.get_data()
    query = callback(f"dl:{data['deadline_picker']['token']}:{action}")
    result = await handle_picker(query, picker_state, VALID_STATES)
    assert result.action == "invalid" and result.deadline is None
    assert query.message.edit_text.await_count == 0


@pytest.mark.parametrize("expired", ["token", "state", "data"])
async def test_expired_or_other_conversation_buttons_are_rejected(picker_state, expired):
    await picker_state.set_state("WorkFlow:edit_due")
    message = SimpleNamespace(answer=AsyncMock())
    await open_picker(message, picker_state)
    old_token = (await picker_state.get_data())["deadline_picker"]["token"]
    if expired == "token":
        await open_picker(message, picker_state)
    elif expired == "state":
        await picker_state.set_state("WorkFlow:comment")
    else:
        await picker_state.clear()
    query = callback(f"dl:{old_token}:none")
    result = await handle_picker(query, picker_state, VALID_STATES)
    assert result.action == "expired"
    assert "expired" in query.answer.call_args.args[0]


@pytest.mark.parametrize("month", ["2028-01", "2028-03"])
async def test_repeated_month_arrow_does_not_send_a_duplicate_calendar(picker_state, month):
    await picker_state.set_state("WorkFlow:task_due")
    await open_picker(
        SimpleNamespace(answer=AsyncMock()),
        picker_state,
        datetime(2028, 2, 10, tzinfo=UTC),
    )
    token = (await picker_state.get_data())["deadline_picker"]["token"]
    query = callback(f"dl:{token}:month:{month}")
    query.message.edit_text.side_effect = [
        None,
        TelegramBadRequest(
            method=EditMessageText(text="Calendar"),
            message="Bad Request: message is not modified",
        ),
    ]
    for _ in range(2):
        assert (await handle_picker(query, picker_state, VALID_STATES)).action == "updated"
    assert (await picker_state.get_data())["deadline_picker"]["month"] == month
    assert query.message.edit_text.await_count == query.answer.await_count == 2
    query.message.answer.assert_not_awaited()
