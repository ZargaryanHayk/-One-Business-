"""Spoken form answers shared by the Telegram process and transcription worker."""

import calendar
import hashlib
import html
import json
import re
import secrets
from datetime import datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import redis
from aiogram.fsm.storage.base import DefaultKeyBuilder, StorageKey

from app.config import CONVERSATION_TTL_SECONDS, settings
from app.task_titles import derive_title

VOICE_ANSWER = "voice_answer"
INPUT_STATES = {
    "WorkFlow:project_name": "project name",
    "WorkFlow:task_project": "project selection",
    "WorkFlow:member_email": "person’s email",
    "WorkFlow:task_assignee": "assignee",
    "WorkFlow:task_due": "deadline",
    "WorkFlow:edit_due": "deadline",
    "WorkFlow:task_title": "task title",
    "WorkFlow:task_content": "task description",
    "WorkFlow:comment": "comment",
    "WorkFlow:edit_title": "task title",
    "WorkFlow:edit_text": "task description",
    "WorkFlow:quick_voice": "personal task",
}
INPUT_LIMITS = {
    "WorkFlow:project_name": 120,
    "WorkFlow:task_project": 120,
    "WorkFlow:member_email": 254,
    "WorkFlow:task_assignee": 254,
    "WorkFlow:task_title": 120,
    "WorkFlow:edit_title": 120,
    "WorkFlow:comment": 4000,
    "WorkFlow:task_due": 100,
    "WorkFlow:edit_due": 100,
}


def editor_key(token: str) -> str:
    return "voice:editor:" + hashlib.sha256(token.encode()).hexdigest()


def editor_origin() -> str:
    """Telegram rejects dotless HTTP button hosts, including localhost."""
    origin = settings().app_origin
    parsed = urlparse(origin)
    if parsed.hostname == "localhost":
        host = "127.0.0.1" + (f":{parsed.port}" if parsed.port is not None else "")
        return parsed._replace(netloc=host).geturl()
    return origin


def review_messages(pending: dict) -> list[dict]:
    """Return the complete transcript, with Cancel and Approve on the last part."""
    value = pending["text"]
    chunks = [value[index : index + 3500] for index in range(0, len(value), 3500)] or [""]
    messages = [{"text": "🎙 Transcription:\n" + html.escape(chunk)} for chunk in chunks]
    messages[-1]["text"] += (
        "\n\nApprove this text, or Cancel to send a new voice note or type your answer."
    )
    messages[-1]["reply_markup"] = {
        "inline_keyboard": [
            [
                {
                    "text": "Cancel",
                    "callback_data": (
                        f"voiceinput:{pending['token']}:cancel:{pending.get('revision', 0)}"
                    ),
                },
                {
                    "text": "Approve",
                    "callback_data": (
                        f"voiceinput:{pending['token']}:approve:{pending.get('revision', 0)}"
                    ),
                },
            ]
        ]
    }
    return messages


def publish_answer(chat_id: int, token: str, owner_id: str, transcript: str) -> dict | None:
    """Update only the still-active question waiting for this recording."""
    key = StorageKey(
        bot_id=int(settings().telegram_bot_token.split(":", 1)[0]),
        chat_id=chat_id,
        user_id=chat_id,
    )
    builder = DefaultKeyBuilder()
    state_key, data_key = builder.build(key, "state"), builder.build(key, "data")
    client = redis.Redis.from_url(settings().redis_url, socket_timeout=5, socket_connect_timeout=5)
    try:
        with client.pipeline() as pipe:
            while True:
                try:
                    pipe.watch(state_key, data_key)
                    current, raw = pipe.get(state_key), pipe.get(data_key)
                    current = current.decode() if current else None
                    data = json.loads(raw) if raw else {}
                    pending = data.get(VOICE_ANSWER) or {}
                    if (
                        current not in INPUT_STATES
                        or current != pending.get("state")
                        or pending.get("token") != token
                        or pending.get("owner_id") != owner_id
                        or pending.get("revision", 0) != 0
                    ):
                        return None
                    pending["text"] = normalize_answer(current, transcript)
                    pending["revision"] = 0
                    pending.setdefault("editor_token", secrets.token_urlsafe(32))
                    data[VOICE_ANSWER] = pending
                    pipe.multi()
                    pipe.set(state_key, current, ex=CONVERSATION_TTL_SECONDS)
                    pipe.set(data_key, json.dumps(data), ex=CONVERSATION_TTL_SECONDS)
                    pipe.set(
                        editor_key(pending["editor_token"]),
                        json.dumps({"chat_id": chat_id, "token": token, "owner_id": owner_id}),
                        ex=CONVERSATION_TTL_SECONDS,
                    )
                    pipe.execute()
                    return pending
                except redis.WatchError:
                    continue
    finally:
        client.close()


def spoken_email(value: str) -> str:
    value = value.strip(" .,!?:;").casefold()
    if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
        return value
    for word, symbol in {
        "at": "@",
        "dot": ".",
        "underscore": "_",
        "dash": "-",
        "plus": "+",
    }.items():
        value = re.sub(rf"\b{word}\b", symbol, value)
    for number, word in enumerate("zero one two three four five six seven eight nine".split()):
        value = re.sub(rf"\b{word}\b", str(number), value)
    return re.sub(r"\s+", "", value)


def normalize_answer(state: str, value: str) -> str:
    if state in {"WorkFlow:task_title", "WorkFlow:edit_title"}:
        return derive_title(value)
    if state in {
        "WorkFlow:task_content",
        "WorkFlow:edit_text",
        "WorkFlow:comment",
        "WorkFlow:quick_voice",
    }:
        return value.strip()
    value = " ".join(value.strip(" .,!?:;").split())
    if state == "WorkFlow:member_email":
        return spoken_email(value)
    if state in {"WorkFlow:task_due", "WorkFlow:edit_due"}:
        return spoken_deadline(value)
    return value


def spoken_deadline(value: str, reference: datetime | None = None) -> str:
    """Recognize common spoken dates; other answers use the normal date validation."""
    original = value
    value = value.casefold().strip(" .,!?:;")
    if value in {"no deadline", "without a deadline", "none", "skip"}:
        return "-"
    reference = reference or datetime.now(ZoneInfo(settings().app_timezone))
    words = {
        **dict(
            enumerate(
                (
                    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
                    "fourteen fifteen sixteen seventeen eighteen nineteen"
                ).split()
            )
        ),
        20: "twenty",
        30: "thirty",
        40: "forty",
        50: "fifty",
    }
    for number, word in sorted(words.items(), reverse=True):
        value = re.sub(rf"\b{word}\b", str(number), value)
    value = re.sub(
        r"\b(20|30|40|50) (\d)\b", lambda match: str(int(match[1]) + int(match[2])), value
    )
    # Spoken hours/minutes such as "six thirty pm" become "6 30 pm".
    parts = re.split(r"\s+at\s+", value, maxsplit=1)
    date_text = re.sub(r"(?<=\d)(st|nd|rd|th)\b", "", parts[0].strip())
    weekday = date_text.removeprefix("next ")
    if date_text == "today":
        day = reference.date()
    elif date_text == "tomorrow":
        day = reference.date() + timedelta(days=1)
    elif match := re.fullmatch(r"in (\d{1,3}) days?", date_text):
        day = reference.date() + timedelta(days=int(match[1]))
    elif weekday in [name.casefold() for name in calendar.day_name]:
        target = [name.casefold() for name in calendar.day_name].index(weekday)
        offset = (target - reference.weekday()) % 7
        if date_text.startswith("next ") and offset == 0:
            offset = 7
        day = reference.date() + timedelta(days=offset)
    else:
        day = None
        for pattern in ("%Y-%m-%d", "%B %d %Y", "%d %B %Y", "%B %d", "%d %B"):
            try:
                candidate = datetime.strptime(date_text, pattern)
                day = (
                    candidate.date()
                    if "%Y" in pattern
                    else candidate.replace(year=reference.year).date()
                )
                break
            except ValueError:
                continue
        if day is None:
            return original
    hour, minute = 23, 59
    if len(parts) == 2:
        clock = re.sub(r"\b([ap])\.?m\.?", r"\1m", parts[1]).replace("o'clock", "").strip()
        match = re.fullmatch(r"(\d{1,2})(?:[: ](\d{1,2}))?\s*(am|pm)?", clock)
        if not match:
            return original
        hour, minute = int(match[1]), int(match[2] or 0)
        if match[3]:
            if not 1 <= hour <= 12:
                return original
            hour = hour % 12 + (12 if match[3] == "pm" else 0)
        if hour > 23 or minute > 59:
            return original
    return f"{day.isoformat()} {hour:02d}:{minute:02d}"
