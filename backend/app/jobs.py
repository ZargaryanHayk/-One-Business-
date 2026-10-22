import html
import json
import logging
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path

import httpx
import redis
from aiogram.fsm.storage.base import DefaultKeyBuilder, StorageKey
from billiard.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from app.celery_app import celery
from app.chat_cleanup import record_http_message
from app.config import CONVERSATION_TTL_SECONDS, settings
from app.db import SessionLocal, engine
from app.domain import outbox, task_event
from app.models import ChatCleanup, Task, TelegramSession, User, VoiceJob, now
from app.task_titles import derive_title
from app.transcription import (
    PermanentVoiceError,
    check_rate_limit,
    download_voice,
    retry_countdown,
    transcribe_audio,
)
from app.voice_input import publish_answer, review_messages
from app.workspace import (
    WorkspaceError,
    assignment,
    project_for,
    save_comment,
    update_task,
    validate_assignment,
)

log = logging.getLogger(__name__)

COMMENT_LIMIT = 4000
DESCRIPTION_LIMIT = 10000
SNIPPET_LIMIT = 300
# Another delivery of the same job holds its lock for at most the 600-second hard limit.
LOCK_BUSY_COUNTDOWN = 60
LOCK_BUSY_MAX_RETRIES = 15
# The Telegram wizard's aiogram FSM lives in Redis (RedisStorage with DefaultKeyBuilder).
# These must match app.project_bot.WorkFlow and RedisStorage(state_ttl/data_ttl) in app.bot.
WIZARD_TTL_SECONDS = CONVERSATION_TTL_SECONDS
TITLE_STATE = "WorkFlow:task_title"
CONTENT_STATE = "WorkFlow:task_content"
# The bot stores the title recording's message ID here; the worker advances only that draft.
VOICE_TITLE_KEY = "voice_title_message"
EDIT_FIELDS = {"edit_title": "title", "edit_text": "text"}
PURPOSES = {
    "task": "voice task",
    "comment": "voice comment",
    "title": "voice title",
    "edit_title": "task title",
    "edit_text": "task description",
    "input": "spoken answer",
}


def fit(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    return value[: limit - 1].rstrip() + "…", True


def snippet(value: str) -> str:
    return html.escape(fit(" ".join(value.split()), SNIPPET_LIMIT)[0])


def buttons(*rows) -> dict:
    return {
        "inline_keyboard": [
            [{"text": label, "callback_data": data} for label, data in row] for row in rows
        ]
    }


def fail_job(job_id: uuid.UUID, generation: int, message: str, retryable: bool = True) -> None:
    with SessionLocal.begin() as db:
        job = db.scalar(select(VoiceJob).where(VoiceJob.id == job_id).with_for_update())
        if not job or job.generation != generation or job.status == "completed":
            return
        job.status, job.error = "failed", message[:256]
        if job.kind == "title":
            notice = (
                f"Could not transcribe your voice title: {message}\n"
                "Type the title, send another voice note, or /cancel."
            )
        elif job.kind == "input":
            notice = (
                f"Could not transcribe your answer: {message}\n"
                "Send another voice note, type your answer, or /cancel."
            )
        elif job.kind in EDIT_FIELDS:
            notice = (
                f"Could not update the {PURPOSES[job.kind]}: {message}\n"
                f"Open task: /task {job.target_task_id}"
            )
        else:
            notice = f"Could not create your {PURPOSES[job.kind]}: {message}"
        # Retrying cannot fix a recording or request that is itself invalid.
        extra = {"retry_job_id": str(job.id)} if retryable else {}
        outbox(
            db,
            "notification",
            f"failed:{job.id}:{generation}",
            chat_id=job.chat_id,
            recipient_id=str(job.user_id),
            created_at=job.created_at.isoformat(),
            text=notice,
            **extra,
        )


@celery.task(
    bind=True, name="app.jobs.transcribe", max_retries=3, soft_time_limit=570, time_limit=600
)
def transcribe(self, job_id: str, generation: int):
    identifier = uuid.UUID(job_id)
    started = time.monotonic()
    # A PostgreSQL session advisory lock survives commits, and is released on worker death.
    lock_key = int.from_bytes(identifier.bytes[:8], "big", signed=True)
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as lock:
        if not lock.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key}):
            # Another delivery is running; recheck once it has finished or been killed.
            raise self.retry(
                countdown=LOCK_BUSY_COUNTDOWN, max_retries=LOCK_BUSY_MAX_RETRIES
            ) from None
        try:
            with SessionLocal.begin() as db:
                job = db.get(VoiceJob, identifier)
                if not job or job.generation != generation or job.status in {"completed", "failed"}:
                    return
                # This persisted counter also bounds redeliveries after abrupt worker loss.
                if job.attempts >= 4:
                    exhausted = True
                else:
                    exhausted = False
                    job.attempts += 1
                    job.status = "processing"
                    attempt, file_id = job.attempts, job.file_id
                    # Show "typing…" as soon as work starts, through the notification queue.
                    outbox(
                        db,
                        "chat_action",
                        f"typing:{job.id}:{generation}:{attempt}",
                        chat_id=job.chat_id,
                        job_id=str(job.id),
                        recipient_id=str(job.user_id),
                    )
            if exhausted:
                fail_job(identifier, generation, "Processing repeatedly interrupted. Please retry.")
                return
            log.info("transcription_started", extra={"job_id": job_id, "attempt": attempt})
            try:
                with tempfile.TemporaryDirectory(prefix="taskbot-") as temporary:
                    path = Path(temporary) / "voice.ogg"
                    download_voice(file_id, path)
                    transcript = transcribe_audio(path)
                complete_job(identifier, generation, transcript)
                log.info(
                    "transcription_completed",
                    extra={
                        "job_id": job_id,
                        "duration_seconds": round(time.monotonic() - started, 2),
                    },
                )
            except PermanentVoiceError as error:
                fail_job(identifier, generation, str(error), retryable=False)
            except WorkspaceError as error:
                fail_job(identifier, generation, error.message, retryable=False)
            except SoftTimeLimitExceeded:
                # The same recording would time out again; never spend the queue on it twice.
                log.warning("transcription_timed_out", extra={"job_id": job_id})
                fail_job(
                    identifier,
                    generation,
                    "Processing timed out. Try a shorter recording.",
                    retryable=False,
                )
            except Exception as error:
                log.warning(
                    "transcription_attempt_failed",
                    extra={
                        "job_id": job_id,
                        "attempt": attempt,
                        "error_type": type(error).__name__,
                    },
                )
                if attempt >= 4 or self.request.retries >= 3:
                    message = "Processing is temporarily unavailable. Please retry."
                    fail_job(identifier, generation, message)
                else:
                    with SessionLocal.begin() as db:
                        job = db.get(VoiceJob, identifier)
                        job.status = "queued"
                    countdown = retry_countdown(error, min(10 * 2 ** (attempt - 1), 120))
                    # Do not put the original exception into Celery's serialized message/logs.
                    raise self.retry(countdown=countdown) from None
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})


def complete_job(identifier: uuid.UUID, generation: int, transcript: str) -> None:
    with SessionLocal() as db:
        job = db.get(VoiceJob, identifier)
        kind, chat_id, message_id = job.kind, job.chat_id, job.message_id
        target_id = job.target_task_id
        input_token = job.input_token
        owner = db.get(User, job.user_id)
    extra: dict = {}
    review_parts = []
    if kind == "input":
        if not transcript.strip() or len(transcript) > 10000:
            raise PermanentVoiceError("Please record an answer using 1–10,000 characters.")
        answer = publish_answer(chat_id, input_token, str(owner.id), transcript)
        confirmation = None
        if answer:
            review_parts = review_messages(answer)
            confirmation = review_parts[-1]["text"]
            extra["reply_markup"] = review_parts[-1]["reply_markup"]
    elif kind == "title":
        title = derive_title(transcript)
        outcome = advance_wizard(chat_id, message_id, title)
        if outcome == "advanced":
            confirmation = (
                f"📝 Title transcribed: <b>{html.escape(title)}</b>\n\n"
                "📄 Step 2 · Task description\n"
                "Send details as text or a voice note, or tap <b>Skip description</b> "
                "if this title is all you need.\n"
                "You’ll choose a deadline before saving."
            )
            extra["reply_markup"] = buttons(
                [("⏩ Skip description", "tasknew:nodescription")],
                [("Cancel", "tasknew:cancel")],
            )
        elif outcome == "expired":
            confirmation = (
                f"🎙 Title transcribed: <b>{html.escape(title)}</b>\n"
                "This task draft was cancelled or expired, so nothing was saved. "
                "Use /newtask to start again."
            )
        else:
            # A typed title or a newer recording replaced this one; stay quiet.
            confirmation = None
    elif kind in EDIT_FIELDS:
        field = EDIT_FIELDS[kind]
        value, shortened = (
            (derive_title(transcript), False)
            if field == "title"
            else fit(transcript, DESCRIPTION_LIMIT)
        )
        # The advisory lock serializes deliveries; update_task rechecks every permission.
        edited = update_task(owner, target_id, {field: value})
        if field == "title":
            confirmation = f"✏️ Task title updated: <b>{html.escape(edited['title'])}</b>"
        else:
            confirmation = (
                f"✏️ Description updated for <b>{html.escape(edited['title'])}</b>\n"
                f"🗒 <i>{snippet(value)}</i>"
            )
            if shortened:
                confirmation += "\n✂️ The transcript was shortened to 10,000 characters."
        extra["reply_markup"] = buttons([("Open task", f"task:{target_id}")])
    with SessionLocal.begin() as db:
        job = db.scalar(select(VoiceJob).where(VoiceJob.id == identifier).with_for_update())
        if job.generation != generation or job.status == "completed":
            return
        if kind == "comment":
            comment_text, shortened = fit(transcript, COMMENT_LIMIT)
            owner = db.get(User, job.user_id)
            save_comment(db, owner, job.comment_task_id, comment_text, job.chat_id, job.message_id)
            task = db.get(Task, job.comment_task_id)
            confirmation = (
                f"💬 Your voice comment was added to <b>{html.escape(task.title)}</b>\n"
                f"🗒 <i>{snippet(comment_text)}</i>\n"
                f"Open task: /task {job.comment_task_id}"
            )
            if shortened:
                confirmation += (
                    "\n✂️ The transcript was longer than 4,000 characters, "
                    "so the comment was shortened."
                )
            extra["reply_markup"] = buttons([("Open task", f"task:{job.comment_task_id}")])
        elif kind == "task":
            task = save_voice_task(db, job, transcript)
            confirmation = (
                f"✅ Voice task created: <b>{html.escape(task.title)}</b>\n"
                f"🗒 <i>{snippet(task.text)}</i>\n"
                "Open /dashboard to manage it."
            )
            extra["reply_markup"] = buttons([("Open task", f"task:{task.id}")])
        job.status, job.error = "completed", None
        for index, part in enumerate(review_parts[:-1]):
            outbox(
                db,
                "notification",
                f"voice-confirm:{identifier}:part:{index}",
                chat_id=job.chat_id,
                recipient_id=str(job.user_id),
                created_at=job.created_at.isoformat(),
                parse_mode="HTML",
                **part,
            )
        if confirmation:
            outbox(
                db,
                "notification",
                f"voice-confirm:{identifier}",
                chat_id=job.chat_id,
                recipient_id=str(job.user_id),
                created_at=job.created_at.isoformat(),
                text=confirmation,
                parse_mode="HTML",
                **extra,
            )


def advance_wizard(chat_id: int, message_id: int, title: str) -> str:
    """Compare-and-set the bot's FSM: still waiting for this recording -> description step."""
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
                    state, raw = pipe.get(state_key), pipe.get(data_key)
                    state = state.decode() if isinstance(state, bytes) else state
                    data = json.loads(raw) if raw else {}
                    if state is None:
                        return "expired"
                    if data.get(VOICE_TITLE_KEY) != message_id:
                        return "superseded"
                    if state == CONTENT_STATE and data.get("title") == title:
                        return "advanced"  # An earlier delivery already advanced this draft.
                    if state != TITLE_STATE:
                        return "superseded"
                    data["title"] = title
                    pipe.multi()
                    pipe.set(state_key, CONTENT_STATE, ex=WIZARD_TTL_SECONDS)
                    pipe.set(data_key, json.dumps(data), ex=WIZARD_TTL_SECONDS)
                    pipe.execute()
                    return "advanced"
                except redis.WatchError:
                    continue
    finally:
        client.close()


def save_voice_task(db, job: VoiceJob, transcript: str) -> Task:
    if job.project_id:
        owner = db.get(User, job.user_id)
        project_for(db, owner, job.project_id, manage=True)
        validate_assignment(db, owner, job.project_id, job.assignee_id)
    task = db.execute(
        insert(Task)
        .values(
            user_id=job.user_id,
            project_id=job.project_id,
            assignee_id=job.assignee_id or (job.user_id if job.project_id is None else None),
            due_at=job.due_at,
            text=transcript,
            title=job.task_title or derive_title(transcript),
            source="voice",
            chat_id=job.chat_id,
            message_id=job.message_id,
        )
        .on_conflict_do_nothing(constraint="uq_task_message")
        .returning(Task)
    ).scalar_one_or_none()
    if task:
        task_event(db, task)
        assignment(db, task)
        return task
    return db.scalar(
        select(Task).where(Task.chat_id == job.chat_id, Task.message_id == job.message_id)
    )


@celery.task(bind=True, name="app.jobs.notify", max_retries=3, soft_time_limit=50, time_limit=60)
def notify(
    self,
    chat_id: int,
    text: str,
    retry_job_id: str | None = None,
    recipient_id: str | None = None,
    created_at: str | None = None,
    reply_markup: dict | None = None,
    parse_mode: str | None = None,
):
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    rows = list((reply_markup or {}).get("inline_keyboard", []))
    if retry_job_id:
        rows.insert(0, [{"text": "Retry transcription", "callback_data": f"retry:{retry_job_id}"}])
    if rows:
        payload["reply_markup"] = {"inline_keyboard": rows}
    try:
        with SessionLocal.begin() as db:
            # Deliberately held during sending so logout cannot interleave with a delivery.
            binding = db.scalar(
                select(TelegramSession)
                .where(
                    TelegramSession.telegram_id == chat_id,
                    TelegramSession.expires_at > now(),
                )
                .with_for_update()
            )
            if binding is None or (recipient_id and str(binding.user_id) != recipient_id):
                return
            if created_at and db.scalar(
                select(ChatCleanup.id)
                .where(
                    ChatCleanup.chat_id == chat_id,
                    ChatCleanup.created_at >= datetime.fromisoformat(created_at),
                )
                .limit(1)
            ):
                return
            response = httpx.post(
                f"https://api.telegram.org/bot{settings().telegram_bot_token}/sendMessage",
                json=payload,
                timeout=20,
            )
            check_rate_limit(response)
            if response.status_code in {400, 403, 404}:
                # Telegram descriptions can include secret editor URLs. Classify locally;
                # never log the raw response or the outgoing transcript/button payload.
                reason = "request_rejected"
                try:
                    description = response.json().get("description", "").casefold()
                    if "url" in description and any(
                        word in description for word in ("invalid", "wrong", "button")
                    ):
                        reason = "invalid_button_url"
                except (ValueError, AttributeError):
                    pass
                log.warning(
                    "notification_rejected",
                    extra={
                        "error_type": f"TelegramHTTP{response.status_code}",
                        "rejection_reason": reason,
                    },
                )
                return
            response.raise_for_status()
            record_http_message(response, db)
    except Exception as error:
        log.warning("notification_failed", extra={"error_type": type(error).__name__})
        if self.request.retries < 3:
            countdown = retry_countdown(error, 10 * 2**self.request.retries)
            raise self.retry(countdown=countdown) from None
        log.error("notification_retries_exhausted")


@celery.task(name="app.jobs.chat_action", soft_time_limit=15, time_limit=20)
def chat_action(
    chat_id: int,
    job_id: str | None = None,
    recipient_id: str | None = None,
    action: str = "typing",
):
    """Best-effort, ephemeral progress indicator; never retried."""
    try:
        with SessionLocal() as db:
            if job_id:
                job = db.get(VoiceJob, uuid.UUID(job_id))
                if job is None or job.status != "processing":
                    return
            binding = db.scalar(
                select(TelegramSession).where(
                    TelegramSession.telegram_id == chat_id,
                    TelegramSession.expires_at > now(),
                )
            )
            if binding is None or (recipient_id and str(binding.user_id) != recipient_id):
                return
        httpx.post(
            f"https://api.telegram.org/bot{settings().telegram_bot_token}/sendChatAction",
            json={"chat_id": chat_id, "action": action},
            timeout=5,
        )
    except Exception as error:
        log.info("chat_action_failed", extra={"error_type": type(error).__name__})


def project_notification(job, operation, *args):
    try:
        operation(*args)
    except Exception as error:
        log.warning("project_notification_failed", extra={"error_type": type(error).__name__})
        if job.request.retries < 3:
            countdown = retry_countdown(error, 10 * 2**job.request.retries)
            raise job.retry(countdown=countdown) from None
        log.error("project_notification_retries_exhausted")


@celery.task(bind=True, name="app.jobs.remind", max_retries=3, soft_time_limit=50, time_limit=60)
def remind(self, reminder_id: str):
    from app.reminders import send_reminder

    project_notification(self, send_reminder, reminder_id)


@celery.task(
    bind=True, name="app.jobs.assignment_notice", max_retries=3, soft_time_limit=50, time_limit=60
)
def assignment_notice(self, task_id: str, recipient_id: str):
    from app.reminders import send_assignment

    project_notification(self, send_assignment, task_id, recipient_id)


@celery.task(
    bind=True, name="app.jobs.cleanup_chat", max_retries=3, soft_time_limit=50, time_limit=60
)
def cleanup_chat(self, cleanup_id: str):
    from app.chat_cleanup import clear_chat

    project_notification(self, clear_chat, cleanup_id)
