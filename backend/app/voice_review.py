"""A short-lived, draft-only text editor; approval always happens in Telegram."""

import json
import re
import secrets

import redis
from aiogram.fsm.storage.base import DefaultKeyBuilder, StorageKey
from sqlalchemy import select

from app.config import CONVERSATION_TTL_SECONDS, settings
from app.db import SessionLocal
from app.domain import outbox
from app.models import TelegramSession, now
from app.voice_input import (
    INPUT_LIMITS,
    INPUT_STATES,
    VOICE_ANSWER,
    editor_key,
    review_messages,
)
from app.workspace import WorkspaceError


def cache():
    return redis.Redis.from_url(settings().redis_url, socket_timeout=5, socket_connect_timeout=5)


def state_keys(chat_id):
    key = StorageKey(
        bot_id=int(settings().telegram_bot_token.split(":", 1)[0]),
        chat_id=chat_id,
        user_id=chat_id,
    )
    builder = DefaultKeyBuilder()
    return builder.build(key, "state"), builder.build(key, "data")


def require_binding(chat_id, owner_id):
    with SessionLocal() as db:
        binding = db.scalar(
            select(TelegramSession).where(
                TelegramSession.telegram_id == chat_id,
                TelegramSession.expires_at > now(),
            )
        )
        if binding is None or str(binding.user_id) != owner_id:
            raise WorkspaceError(
                410, "This editor expired. Return to Telegram and send your answer again."
            )


def resolve_editor(token):
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise WorkspaceError(410, "This editor link is invalid or expired.")
    with cache() as client:
        raw = client.get(editor_key(token))
        if not raw:
            raise WorkspaceError(410, "This editor expired. Return to Telegram.")
        context = json.loads(raw)
        current, raw_data = client.mget(state_keys(context["chat_id"]))
        current = current.decode() if current else None
        pending = (json.loads(raw_data) if raw_data else {}).get(VOICE_ANSWER) or {}
        if (
            current not in INPUT_STATES
            or current != pending.get("state")
            or pending.get("token") != context["token"]
            or pending.get("owner_id") != context["owner_id"]
            or pending.get("editor_token") != token
            or not pending.get("text")
        ):
            raise WorkspaceError(410, "This transcript is no longer waiting for approval.")
    require_binding(context["chat_id"], context["owner_id"])
    return context["chat_id"], pending


def editor_view(token):
    _, pending = resolve_editor(token)
    return {
        "text": pending["text"],
        "revision": pending.get("revision", 0),
        "label": INPUT_STATES[pending["state"]],
        "max_length": INPUT_LIMITS.get(pending["state"], 10000),
    }


def save_pending_text(chat_id, expected, text, revision):
    if not text.strip() or "\x00" in text:
        raise WorkspaceError(422, "Enter your corrected text.")
    maximum = INPUT_LIMITS.get(expected["state"], 10000)
    if len(text) > maximum:
        raise WorkspaceError(422, f"Keep this answer within {maximum:,} characters.")
    require_binding(chat_id, expected["owner_id"])
    state_key, data_key = state_keys(chat_id)
    with cache() as client, client.pipeline() as pipe:
        while True:
            try:
                pipe.watch(state_key, data_key)
                current, raw_data = pipe.get(state_key), pipe.get(data_key)
                current = current.decode() if current else None
                data = json.loads(raw_data) if raw_data else {}
                pending = data.get(VOICE_ANSWER) or {}
                if (
                    current != expected["state"]
                    or pending.get("token") != expected["token"]
                    or pending.get("owner_id") != expected["owner_id"]
                    or not pending.get("text")
                ):
                    raise WorkspaceError(410, "This transcript is no longer waiting for approval.")
                current_revision = pending.get("revision", 0)
                if current_revision != revision:
                    if current_revision == revision + 1 and pending["text"] == text:
                        return pending
                    raise WorkspaceError(
                        409, "The transcript changed. Reload this editor before saving."
                    )
                pending.update(text=text, revision=revision + 1)
                pending.setdefault("editor_token", secrets.token_urlsafe(32))
                data[VOICE_ANSWER] = pending
                pipe.multi()
                pipe.set(state_key, current, ex=CONVERSATION_TTL_SECONDS)
                pipe.set(data_key, json.dumps(data), ex=CONVERSATION_TTL_SECONDS)
                pipe.set(
                    editor_key(pending["editor_token"]),
                    json.dumps(
                        {
                            "chat_id": chat_id,
                            "token": pending["token"],
                            "owner_id": pending["owner_id"],
                        }
                    ),
                    ex=CONVERSATION_TTL_SECONDS,
                )
                pipe.execute()
                return pending
            except redis.WatchError:
                continue


def save_editor(token, text, revision):
    chat_id, pending = resolve_editor(token)
    pending = save_pending_text(chat_id, pending, text, revision)
    with SessionLocal.begin() as db:
        for index, part in enumerate(review_messages(pending)):
            outbox(
                db,
                "notification",
                f"voice-edited:{pending['token']}:{pending['revision']}:{index}",
                chat_id=chat_id,
                recipient_id=pending["owner_id"],
                created_at=now().isoformat(),
                parse_mode="HTML",
                **part,
            )
    return {
        "revision": pending["revision"],
        "message": "Sent to Telegram. Review the corrected text and tap Approve.",
    }
