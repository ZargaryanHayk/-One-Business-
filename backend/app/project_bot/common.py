"""Conversation states, keyboards, and input parsing shared by the project handlers."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app import workspace as ws
from app.config import settings

PROJECTS = "🗂 Projects"
DUE = "⏰ Due soon"
PROJECT_PAGE = 40
MEMBER_LIMIT = 25
LIST_LIMIT = 200
CANCELLED = "Cancelled. Use /projects, /tasks, or /due."
VOICE_LIMIT = "Please send a voice note up to 5 minutes and 20 MB."
TITLE_PROMPT = (
    "📝 Step 1 · Task title\n"
    "Type a short headline (1–120 characters) or send a voice note directly, or /cancel."
)


class WorkFlow(StatesGroup):
    project_name = State()
    member_email = State()
    task_project = State()
    task_title = State()
    task_content = State()
    task_assignee = State()
    task_due = State()
    quick_voice = State()
    comment = State()
    edit_due = State()
    edit_title = State()
    edit_text = State()


DEADLINE_STATES = {WorkFlow.task_due.state, WorkFlow.edit_due.state}


def keyboard(rows):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=value) for label, value in row]
            for row in rows
        ]
    )


def cancel_keyboard():
    return keyboard([[("Cancel", "tasknew:cancel")]])


def voice_too_large(media):
    return (getattr(media, "duration", 0) or 0) > settings().max_voice_seconds or (
        media.file_size or 0
    ) > settings().max_voice_bytes


def parse_due(value: str):
    if value.strip().casefold() in {"-", "none", "skip"}:
        return None
    value = value.strip()
    try:
        # Date-only input in any ISO form (2026-10-22 or 20261022) uses the end of the day.
        parsed = datetime.combine(date.fromisoformat(value), time(23, 59))
    except ValueError:
        parsed = datetime.fromisoformat(value)
    parsed = parsed if parsed.tzinfo else parsed.replace(tzinfo=ZoneInfo(settings().app_timezone))
    # Raises WorkspaceError("Deadline must be between 1970 and 2100") like the server.
    ws.validate_due(parsed)
    return parsed


def due_label(value):
    return (
        value.astimezone(ZoneInfo(settings().app_timezone)).strftime("%Y-%m-%d %H:%M")
        if value
        else "No deadline"
    )


def require_project_manager(account, identifier):
    from app.db import SessionLocal

    with SessionLocal() as db:
        ws.project_for(db, account, identifier, manage=True)
