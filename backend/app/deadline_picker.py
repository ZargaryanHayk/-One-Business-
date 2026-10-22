"""Telegram date and time selection using inline buttons and expiring FSM state."""

import calendar
import secrets
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings

MIN_YEAR, MAX_YEAR = 1970, 2100


@dataclass(frozen=True)
class PickerResult:
    action: str
    deadline: datetime | None = None


def local_now():
    return datetime.now(ZoneInfo(settings().app_timezone))


def markup(rows, token):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=label, callback_data=f"dl:{token}:{value}")
                for label, value in row
            ]
            for row in rows
        ]
    )


def calendar_view(picker: dict):
    first = date.fromisoformat(picker["month"] + "-01")
    previous = (
        first.replace(year=first.year - 1, month=12)
        if first.month == 1
        else first.replace(month=first.month - 1)
    )
    following = (
        first.replace(year=first.year + 1, month=1)
        if first.month == 12
        else first.replace(month=first.month + 1)
    )
    rows = [
        [
            ("‹", f"month:{previous:%Y-%m}" if previous.year >= MIN_YEAR else "noop"),
            (f"{calendar.month_name[first.month]} {first.year}", "noop"),
            ("›", f"month:{following:%Y-%m}" if following.year <= MAX_YEAR else "noop"),
        ],
        [(day, "noop") for day in ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")],
    ]
    today = local_now().date()
    for week in calendar.Calendar(firstweekday=0).monthdayscalendar(first.year, first.month):
        row = []
        for day in week:
            if not day:
                row.append(("·", "noop"))
                continue
            value = first.replace(day=day)
            label = str(day)
            if value.isoformat() == picker.get("selected"):
                label = f"✓ {day}"
            elif value == today:
                label = f"· {day} ·"
            row.append((label, f"day:{value.isoformat()}"))
        rows.append(row)
    rows += [
        [
            ("Today", f"day:{today.isoformat()}"),
            ("Tomorrow", f"day:{date.fromordinal(today.toordinal() + 1).isoformat()}"),
        ],
        [("No deadline", "none"), ("Cancel", "cancel")],
    ]
    text = (
        f"📅 Choose a deadline date\n{settings().app_timezone}\n"
        "Select a day, then choose the time.\n"
        "Or send a voice note, for example ‘tomorrow at six pm’ or ‘no deadline’."
    )
    return text, markup(rows, picker["token"])


def time_view(picker: dict):
    minutes = picker["minutes"]
    selected = date.fromisoformat(picker["selected"])
    clock = f"{minutes // 60:02d}:{minutes % 60:02d}"
    text = (
        f"🕒 Deadline: {selected:%d %B %Y}\nTime: {clock} ({settings().app_timezone})\n"
        "Choose a time, then save."
    )
    rows = [
        [("−1 hour", "adjust:-60"), (clock, "noop"), ("+1 hour", "adjust:60")],
        [
            ("−15 min", "adjust:-15"),
            ("−5 min", "adjust:-5"),
            ("+5 min", "adjust:5"),
            ("+15 min", "adjust:15"),
        ],
        [("−1 min", "adjust:-1"), ("+1 min", "adjust:1")],
        [
            ("09:00", "time:540"),
            ("12:00", "time:720"),
            ("18:00", "time:1080"),
            ("23:59", "time:1439"),
        ],
        [("‹ Change date", "calendar"), ("✓ Save deadline", "save")],
        [("No deadline", "none"), ("Cancel", "cancel")],
    ]
    return text, markup(rows, picker["token"])


async def open_picker(message, state, initial: datetime | None = None):
    initial = initial.astimezone(ZoneInfo(settings().app_timezone)) if initial else None
    month = initial or local_now()
    if not MIN_YEAR <= month.year <= MAX_YEAR:
        month = local_now()
    picker = {
        "token": secrets.token_hex(4),
        "month": month.strftime("%Y-%m"),
        "selected": initial.date().isoformat() if initial else None,
        "minutes": initial.hour * 60 + initial.minute if initial else 1439,
    }
    await state.update_data(deadline_picker=picker)
    text, buttons = calendar_view(picker)
    await message.answer(text, reply_markup=buttons)


async def handle_picker(query, state, valid_states: set[str]) -> PickerResult:
    data = await state.get_data()
    picker = data.get("deadline_picker")
    parts = (query.data or "").split(":", 3)
    if (
        await state.get_state() not in valid_states
        or not picker
        or len(parts) < 3
        or parts[1] != picker["token"]
    ):
        await query.answer("This calendar expired. Open Set deadline again.")
        return PickerResult("expired")
    action = parts[2]
    value = parts[3] if len(parts) == 4 else None
    if action == "noop":
        await query.answer()
        return PickerResult("updated")
    if action in {"cancel", "none"}:
        await query.answer()
        return PickerResult(action)
    mode = "calendar"
    try:
        if action == "month":
            month = date.fromisoformat(value + "-01")
            if not MIN_YEAR <= month.year <= MAX_YEAR:
                raise ValueError
            picker["month"] = month.strftime("%Y-%m")
        elif action == "day":
            selected = date.fromisoformat(value)
            if not MIN_YEAR <= selected.year <= MAX_YEAR:
                raise ValueError
            picker["selected"] = selected.isoformat()
            picker["month"] = selected.strftime("%Y-%m")
            mode = "time"
        elif action in {"adjust", "time", "save"}:
            selected = date.fromisoformat(picker["selected"])
            if action == "save":
                deadline = datetime(
                    selected.year,
                    selected.month,
                    selected.day,
                    picker["minutes"] // 60,
                    picker["minutes"] % 60,
                    tzinfo=ZoneInfo(settings().app_timezone),
                )
                await query.answer()
                return PickerResult("save", deadline)
            minutes = int(value)
            if action == "adjust":
                if minutes not in {-60, -15, -5, -1, 1, 5, 15, 60}:
                    raise ValueError
                picker["minutes"] = (picker["minutes"] + minutes) % 1440
            else:
                if not 0 <= minutes < 1440:
                    raise ValueError
                if picker["minutes"] == minutes:
                    await query.answer()
                    return PickerResult("updated")
                picker["minutes"] = minutes
            mode = "time"
        elif action != "calendar":
            raise ValueError
    except (TypeError, ValueError, KeyError):
        await query.answer("Invalid date or time. Choose one of the calendar buttons.")
        return PickerResult("invalid")
    await state.update_data(deadline_picker=picker)
    text, buttons = time_view(picker) if mode == "time" else calendar_view(picker)
    await query.answer()
    try:
        await query.message.edit_text(text, reply_markup=buttons)
    except TelegramAPIError as error:
        # A second click can request the month already displayed. Telegram rejects
        # that identical edit; it is not a reason to send another calendar.
        if "message is not modified" not in error.message.lower():
            await query.message.answer(text, reply_markup=buttons)
    return PickerResult("updated")


async def close_picker(message, text):
    try:
        await message.edit_text(text, reply_markup=None)
    except TelegramAPIError:
        # The saved task is already durable even if the old picker cannot be edited.
        pass
