"""Delete known private-chat messages after confirmed logout; never store message text."""

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import redis
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.fsm.storage.base import DefaultKeyBuilder, StorageKey
from aiogram.types import Message
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from app.bot_ui import PUBLIC_COMMANDS
from app.config import settings
from app.db import SessionLocal
from app.models import ChatCleanup, TelegramMessage, TelegramSession, now
from app.transcription import check_rate_limit

LEGACY_SCAN_LIMIT = 1000


def recent_ids_through(message_id):
    return range(max(1, message_id - LEGACY_SCAN_LIMIT + 1), message_id + 1)


def record_message(chat_id, message_id, sent_at, db=None):
    if not isinstance(chat_id, int) or chat_id <= 0:
        return
    if not isinstance(message_id, int) or message_id <= 0:
        return
    statement = (
        insert(TelegramMessage)
        .values(chat_id=chat_id, message_id=message_id, sent_at=sent_at)
        .on_conflict_do_nothing()
    )
    if db is not None:
        db.execute(statement)
    else:
        with SessionLocal.begin() as transaction:
            transaction.execute(statement)


def record_http_message(response, db=None):
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("result"), dict):
        return
    message = payload["result"]
    chat = message.get("chat", {})
    if chat.get("type") != "private" or not isinstance(message.get("date"), int):
        return
    record_message(
        chat.get("id"),
        message.get("message_id"),
        datetime.fromtimestamp(message["date"], UTC),
        db,
    )


class TrackOutgoingMessages(BaseRequestMiddleware):
    async def __call__(self, make_request, bot, method):
        result = await make_request(bot, method)
        messages = result if isinstance(result, list) else [result]
        for message in messages:
            if isinstance(message, Message) and message.chat.type == "private":
                await asyncio.to_thread(
                    record_message, message.chat.id, message.message_id, message.date
                )
        return result


def schedule_cleanup(db, chat_id):
    from app.domain import outbox

    cutoff = now()
    message_ids = list(
        db.scalars(
            select(TelegramMessage.message_id)
            .where(
                TelegramMessage.chat_id == chat_id,
                TelegramMessage.sent_at > cutoff - timedelta(hours=48),
            )
            .order_by(TelegramMessage.message_id.desc())
        )
    )
    first_cleanup = (
        db.scalar(select(ChatCleanup.id).where(ChatCleanup.chat_id == chat_id).limit(1)) is None
    )
    # Before tracking was installed, Telegram supplied no history API. On first cleanup,
    # try the bounded preceding ID range in this private chat; Telegram enforces its age limit.
    if first_cleanup and message_ids:
        message_ids = sorted(
            set(message_ids) | set(recent_ids_through(max(message_ids))), reverse=True
        )
    job = ChatCleanup(
        chat_id=chat_id, message_ids=message_ids, created_at=cutoff, scan_legacy=first_cleanup
    )
    db.add(job)
    db.flush()
    outbox(db, "chat_cleanup", f"chat-cleanup:{job.id}", cleanup_id=str(job.id))
    return job.id


def schedule_account_cleanup(db, user_id):
    for chat_id in db.scalars(
        select(TelegramSession.telegram_id).where(TelegramSession.user_id == user_id)
    ):
        schedule_cleanup(db, chat_id)


def telegram_call(method, payload):
    response = httpx.post(
        f"https://api.telegram.org/bot{settings().telegram_bot_token}/{method}",
        json=payload,
        timeout=10,
    )
    check_rate_limit(response)
    if response.status_code not in {400, 403, 404}:
        response.raise_for_status()
    return response


def reset_chat(db, job):
    # A new conversation must survive cleanup of the previous session.
    newer_message = db.scalar(
        select(TelegramMessage.message_id)
        .where(
            TelegramMessage.chat_id == job.chat_id,
            TelegramMessage.observed_at > job.created_at,
        )
        .limit(1)
    )
    if newer_message is None:
        cache = redis.Redis.from_url(settings().redis_url)
        try:
            bot_id = int(settings().telegram_bot_token.split(":", 1)[0])
            key = StorageKey(bot_id=bot_id, chat_id=job.chat_id, user_id=job.chat_id)
            builder = DefaultKeyBuilder()
            cache.delete(builder.build(key, "state"), builder.build(key, "data"))
        finally:
            cache.close()
        binding = db.get(TelegramSession, job.chat_id)
        if binding is None or binding.expires_at <= now():
            telegram_call(
                "setMyCommands",
                {
                    "scope": {"type": "chat", "chat_id": job.chat_id},
                    "commands": [command.model_dump() for command in PUBLIC_COMMANDS],
                },
            )
        # Telegram requires a new message to dismiss an existing reply keyboard.
        response = telegram_call(
            "sendMessage",
            {
                "chat_id": job.chat_id,
                "text": "Chat cleared.",
                "reply_markup": {"remove_keyboard": True},
                "disable_notification": True,
            },
        )
        if response.status_code == 200:
            record_http_message(response, db)
            payload = response.json()
            result = payload.get("result", {}) if isinstance(payload, dict) else {}
            message_id = result.get("message_id")
            if isinstance(message_id, int) and message_id > 0:
                extra_ids = recent_ids_through(message_id) if job.scan_legacy else [message_id]
                job.message_ids = sorted(set(job.message_ids) | set(extra_ids), reverse=True)
    job.reset_done = True


def clear_chat(cleanup_id):
    identifier = uuid.UUID(cleanup_id)
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        with SessionLocal.begin() as db:
            job = db.scalar(
                select(ChatCleanup)
                .where(ChatCleanup.id == identifier)
                .with_for_update(skip_locked=True)
            )
            if job is None or job.completed_at is not None:
                return
            if not job.reset_done:
                reset_chat(db, job)
                continue
            if not job.message_ids:
                job.completed_at = now()
                return
            batch = job.message_ids[: 1 if job.single_delete else 100]
            response = telegram_call(
                "deleteMessage" if job.single_delete else "deleteMessages",
                {
                    "chat_id": job.chat_id,
                    **({"message_id": batch[0]} if job.single_delete else {"message_ids": batch}),
                },
            )
            if response.status_code == 400 and not job.single_delete:
                # One undeletable service/dice/expired message must not block the rest.
                job.single_delete = True
                continue
            db.execute(
                delete(TelegramMessage).where(
                    TelegramMessage.chat_id == job.chat_id,
                    TelegramMessage.message_id.in_(batch),
                )
            )
            job.message_ids = job.message_ids[len(batch) :]
    # Continue through the durable outbox rather than exceeding the worker time limit.
    from app.domain import outbox

    with SessionLocal.begin() as db:
        outbox(
            db,
            "chat_cleanup",
            f"chat-cleanup-resume:{identifier}:{uuid.uuid4()}",
            cleanup_id=cleanup_id,
        )
