"""Voice/queue pipeline reliability: wizard voice via the worker, retries, outbox, reminders."""

import os
import time
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import redis
from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import Audio, CallbackQuery, Chat, Message, Voice
from aiogram.types import User as TelegramUser
from billiard.exceptions import SoftTimeLimitExceeded
from celery.exceptions import Retry
from sqlalchemy import event, func, select, text

from app import jobs
from app import workspace as ws
from app.auth import telegram_user
from app.celery_app import sweep_stale_temporary
from app.config import settings
from app.db import SessionLocal, engine
from app.dispatcher import OUTBOX_MAX_ATTEMPTS, dispatch_once, recover_stale_jobs, run_periodic
from app.domain import outbox, queue_voice, queue_voice_edit, retry_voice
from app.jobs import chat_action, notify, transcribe
from app.models import Outbox, Task, TaskComment, TaskReminder, User, VoiceJob, now
from app.project_bot import WorkFlow, callback, conversation
from app.reminders import MAX_ATTEMPTS, schedule_reminders
from app.transcription import PermanentVoiceError, TelegramRetryAfter, download_voice

TOKEN = "123456:synthetic_token_for_tests"


@pytest.fixture
async def wizard():
    """The bot's real FSM backend (Redis), as the worker sees it."""
    storage = RedisStorage.from_url(os.environ["REDIS_URL"], state_ttl=300, data_ttl=300)
    session = AsyncMock(return_value=SimpleNamespace())
    bot = Bot(TOKEN, session=session)
    state = FSMContext(storage=storage, key=StorageKey(bot_id=123456, chat_id=100, user_id=100))
    yield SimpleNamespace(state=state, session=session, bot=bot, account=telegram_user(100))
    await storage.close()


def incoming(bot, message_id, text=None, media=None, user_id=100):
    return Message(
        message_id=message_id,
        date=now(),
        chat=Chat(id=user_id, type="private"),
        from_user=TelegramUser(id=user_id, is_bot=False, first_name="Tester"),
        text=text,
        **(media or {}),
    ).as_(bot)


def press(bot, data, user_id=100):
    message = incoming(bot, 1, text="menu")
    return CallbackQuery(
        id="cb",
        chat_instance="test",
        data=data,
        message=message,
        from_user=TelegramUser(id=user_id, is_bot=False, first_name="Tester"),
    ).as_(bot)


def sent_text(session):
    return session.call_args.args[1].text


def voice(file_id="recording", duration=4):
    return {"voice": Voice(file_id=file_id, file_unique_id=file_id, duration=duration)}


def run_worker(monkeypatch, identifier, transcript, generation=0):
    monkeypatch.setattr("app.jobs.download_voice", Mock())
    convert = Mock(return_value=transcript)
    monkeypatch.setattr("app.jobs.transcribe_audio", convert)
    transcribe.run(str(identifier), generation)
    return convert


def notifications():
    """Voice-job notifications (not the confirmations of typed tasks used as fixtures)."""
    with SessionLocal() as db:
        return [
            row.payload
            for row in db.scalars(
                select(Outbox).where(Outbox.kind == "notification").order_by(Outbox.created_at)
            )
            if row.dedupe_key.startswith(("voice-confirm:", "failed:"))
        ]


def callbacks(payload):
    rows = (payload.get("reply_markup") or {}).get("inline_keyboard", [])
    return [button["callback_data"] for row in rows for button in row if "callback_data" in button]


async def approve_wizard_voice(wizard):
    pending = (await wizard.state.get_data())["voice_answer"]
    await callback(
        press(wizard.bot, f"voiceinput:{pending['token']}:approve:{pending.get('revision', 0)}"),
        wizard.account,
        wizard.state,
    )


def test_worker_state_names_match_the_bot_wizard():
    assert WorkFlow.task_title.state == jobs.TITLE_STATE
    assert WorkFlow.task_content.state == jobs.CONTENT_STATE
    import app.project_bot

    # The bot process no longer runs Whisper in its event loop.
    assert not hasattr(app.project_bot, "transcribe_telegram_voice")


async def test_wizard_voice_title_is_queued_then_worker_advances_the_draft(monkeypatch, wizard):
    await wizard.state.set_state(WorkFlow.task_title)
    await conversation(incoming(wizard.bot, 710, media=voice()), wizard.account, wizard.state)
    assert "Got it" in sent_text(wizard.session)
    assert await wizard.state.get_state() == WorkFlow.task_title.state
    with SessionLocal() as db:
        job = db.scalar(select(VoiceJob))
        assert job.kind == "input" and job.status == "queued" and job.message_id == 710
        assert db.scalar(select(Outbox).where(Outbox.kind == "transcribe")) is not None
        assert db.scalar(select(func.count()).select_from(Task)) == 0

    run_worker(monkeypatch, job.id, "Buy groceries from the store")
    assert await wizard.state.get_state() == WorkFlow.task_title.state
    [prompt] = notifications()
    assert "Transcription" in prompt["text"] and "Buy groceries from the store" in prompt["text"]
    assert prompt["parse_mode"] == "HTML"
    assert len(callbacks(prompt)) == 2
    assert ":cancel:0" in callbacks(prompt)[0] and ":approve:0" in callbacks(prompt)[1]
    assert [b["text"] for b in prompt["reply_markup"]["inline_keyboard"][0]] == [
        "Cancel",
        "Approve",
    ]
    assert "retry_job_id" not in prompt
    with SessionLocal() as db:
        assert db.get(VoiceJob, job.id).status == "completed"
        assert db.scalar(select(Outbox).where(Outbox.kind == "chat_action")) is not None
    # A duplicate delivery neither re-transcribes nor re-sends the prompt.
    assert run_worker(monkeypatch, job.id, "Different").call_count == 0
    assert len(notifications()) == 1

    await approve_wizard_voice(wizard)
    assert await wizard.state.get_state() == WorkFlow.task_content.state
    assert (await wizard.state.get_data())["title"] == "Buy groceries from the store"

    await callback(press(wizard.bot, "tasknew:nodescription"), wizard.account, wizard.state)
    assert await wizard.state.get_state() == WorkFlow.task_due.state
    await conversation(incoming(wizard.bot, 715, text="-"), wizard.account, wizard.state)
    with SessionLocal() as db:
        task = db.scalar(select(Task))
        assert task.title == task.text == "Buy groceries from the store"


async def test_wizard_title_prompt_escapes_html(monkeypatch, wizard):
    await wizard.state.set_state(WorkFlow.task_title)
    await conversation(incoming(wizard.bot, 711, media=voice()), wizard.account, wizard.state)
    with SessionLocal() as db:
        identifier = db.scalar(select(VoiceJob.id))
    run_worker(monkeypatch, identifier, "Fix <b>bold</b> & tags")
    assert "Fix &lt;b&gt;bold&lt;/b&gt; &amp; tags" in notifications()[0]["text"]


async def test_typed_title_supersedes_pending_voice_title(monkeypatch, wizard):
    await wizard.state.set_state(WorkFlow.task_title)
    await conversation(incoming(wizard.bot, 712, media=voice()), wizard.account, wizard.state)
    await conversation(incoming(wizard.bot, 713, text="Typed title"), wizard.account, wizard.state)
    with SessionLocal() as db:
        identifier = db.scalar(select(VoiceJob.id))
    run_worker(monkeypatch, identifier, "Spoken title")
    assert await wizard.state.get_state() == WorkFlow.task_content.state
    assert (await wizard.state.get_data())["title"] == "Typed title"
    assert notifications() == []
    with SessionLocal() as db:
        assert db.get(VoiceJob, identifier).status == "completed"


async def test_voice_title_for_cancelled_draft_reports_it(monkeypatch, wizard):
    await wizard.state.set_state(WorkFlow.task_title)
    await conversation(incoming(wizard.bot, 714, media=voice()), wizard.account, wizard.state)
    await wizard.state.clear()
    with SessionLocal() as db:
        identifier = db.scalar(select(VoiceJob.id))
    run_worker(monkeypatch, identifier, "Spoken title")
    assert await wizard.state.get_state() is None
    assert notifications() == []


async def test_wizard_voice_title_failure_keeps_title_step(monkeypatch, wizard):
    await wizard.state.set_state(WorkFlow.task_title)
    await conversation(incoming(wizard.bot, 715, media=voice()), wizard.account, wizard.state)
    with SessionLocal() as db:
        identifier = db.scalar(select(VoiceJob.id))
    monkeypatch.setattr("app.jobs.download_voice", Mock())
    monkeypatch.setattr(
        "app.jobs.transcribe_audio", Mock(side_effect=PermanentVoiceError("No speech detected."))
    )
    transcribe.run(str(identifier), 0)
    [notice] = notifications()
    assert "transcribe your answer" in notice["text"] and "type your answer" in notice["text"]
    assert "retry_job_id" not in notice
    assert await wizard.state.get_state() == WorkFlow.task_title.state
    await conversation(incoming(wizard.bot, 716, text="Typed"), wizard.account, wizard.state)
    assert (await wizard.state.get_data())["title"] == "Typed"


@pytest.mark.parametrize("field", ["title", "text"])
@pytest.mark.parametrize("media_type", ["voice", "audio"])
async def test_voice_edit_is_queued_and_applied_by_worker(monkeypatch, wizard, field, media_type):
    from app.domain import create_text_task

    target = create_text_task(100, "Alex", 100, 700, "Original description")
    other = create_text_task(100, "Alex", 100, 701, "Other task")
    await wizard.state.set_state(WorkFlow.edit_title if field == "title" else WorkFlow.edit_text)
    await wizard.state.update_data(edit_task=str(target))
    media_class = Voice if media_type == "voice" else Audio
    media = {media_type: media_class(file_id="edit", file_unique_id="edit", duration=5)}
    await conversation(incoming(wizard.bot, 702, media=media), wizard.account, wizard.state)
    assert "Got it" in sent_text(wizard.session)
    assert await wizard.state.get_state() == (
        WorkFlow.edit_title.state if field == "title" else WorkFlow.edit_text.state
    )
    with SessionLocal() as db:
        job = db.scalar(select(VoiceJob))
        assert job.kind == "input"
        assert db.get(Task, target).text == "Original description"
    transcript = "Spoken headline\n" + "complete details " * 12
    run_worker(monkeypatch, job.id, transcript)
    with SessionLocal() as db:
        assert db.get(Task, target).text == "Original description"
    await approve_wizard_voice(wizard)
    with SessionLocal() as db:
        task = db.get(Task, target)
        if field == "title":
            assert task.title == " ".join(transcript.split())[:119].rstrip() + "…"
            assert task.text == "Original description"
        else:
            assert task.text == transcript.strip() and task.title == "Original description"
        assert db.get(Task, other).text == "Other task"
        assert db.scalar(select(func.count()).select_from(Task)) == 2
        assert db.get(VoiceJob, job.id).status == "completed"
    [notice] = notifications()
    assert "Transcription" in notice["text"] and ":approve:0" in callbacks(notice)[1]


def test_voice_edit_requires_manager_when_queued_and_when_applied(monkeypatch):
    with SessionLocal() as db:
        manager = db.scalar(select(User).where(User.telegram_id == 100))
        developer = db.scalar(select(User).where(User.telegram_id == 200))
    project = ws.create_project(manager, "Edits")
    ws.add_member(manager, project["id"], developer.email)
    task = ws.create_task(manager, "Spec", project_id=project["id"], assignee_id=developer.id)
    with pytest.raises(ws.WorkspaceError) as error:
        queue_voice_edit(200, "Jamie", 200, 5, "audio", task["id"], "title")
    assert error.value.status == 403
    identifier = queue_voice_edit(100, "Alex", 100, 6, "audio", task["id"], "title")
    ws.update_task(manager, task["id"], {"status": "completed"})
    ws.update_task(manager, task["id"], {"archived": True})
    run_worker(monkeypatch, identifier, "New title")
    with SessionLocal() as db:
        assert db.get(VoiceJob, identifier).status == "failed"
        assert db.get(Task, task["id"]).title == "Spec"
    [notice] = notifications()
    assert "Restore this task" in notice["text"] and "retry_job_id" not in notice


def test_long_voice_comment_is_truncated_and_reported(monkeypatch):
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.telegram_id == 100))
    task = ws.create_task(owner, "Commented task")
    identifier = queue_voice(100, "Alex", 100, 9, "audio", comment_task_id=task["id"])
    run_worker(monkeypatch, identifier, "word " * 1200)
    with SessionLocal() as db:
        comment = db.scalar(select(TaskComment))
        assert len(comment.text) == 4000 and comment.text.endswith("…")
        assert db.get(VoiceJob, identifier).status == "completed"
    [notice] = notifications()
    assert "shortened" in notice["text"] and "Commented task" in notice["text"]
    assert len(notice["text"]) < 4096


def test_voice_task_confirmation_names_task_and_snippet(monkeypatch):
    identifier = queue_voice(100, "Alex", 100, 10, "audio")
    run_worker(monkeypatch, identifier, "Call <Ana> about the budget " + "x" * 600)
    [notice] = notifications()
    assert "Voice task created" in notice["text"] and "&lt;Ana&gt;" in notice["text"]
    assert len(notice["text"]) < 1200 and notice["parse_mode"] == "HTML"
    with SessionLocal() as db:
        assert callbacks(notice) == [f"task:{db.scalar(select(Task.id))}"]


@pytest.mark.parametrize(
    "error,retryable",
    [
        (PermanentVoiceError("Voice note exceeds the 5 minute limit."), False),
        (PermanentVoiceError("Could not decode this voice note."), False),
        (SoftTimeLimitExceeded(), False),
        (httpx.ConnectError("offline"), True),
    ],
)
def test_retry_button_only_for_failures_retry_can_fix(monkeypatch, error, retryable):
    identifier = queue_voice(100, "Alex", 100, 11, "audio")
    monkeypatch.setattr("app.jobs.download_voice", Mock(side_effect=error))
    retry = Mock(side_effect=Retry())
    monkeypatch.setattr(transcribe, "retry", retry)
    if retryable:
        with SessionLocal.begin() as db:
            db.get(VoiceJob, identifier).attempts = 3
    transcribe.run(str(identifier), 0)
    assert not retry.called
    with SessionLocal() as db:
        job = db.get(VoiceJob, identifier)
        assert job.status == "failed" and job.attempts == (4 if retryable else 1)
    [notice] = notifications()
    assert ("retry_job_id" in notice) is retryable
    if isinstance(error, SoftTimeLimitExceeded):
        assert "timed out" in notice["text"]


def test_rate_limited_download_waits_as_telegram_requests(monkeypatch):
    identifier = queue_voice(100, "Alex", 100, 12, "audio")
    monkeypatch.setattr("app.jobs.download_voice", Mock(side_effect=TelegramRetryAfter(95)))
    retry = Mock(side_effect=Retry())
    monkeypatch.setattr(transcribe, "retry", retry)
    with pytest.raises(Retry):
        transcribe.run(str(identifier), 0)
    assert retry.call_args.kwargs["countdown"] == 95


def test_busy_job_lock_retries_later_instead_of_dropping(monkeypatch):
    identifier = queue_voice(100, "Alex", 100, 13, "audio")
    retry = Mock(side_effect=Retry())
    monkeypatch.setattr(transcribe, "retry", retry)
    key = int.from_bytes(identifier.bytes[:8], "big", signed=True)
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as other:
        assert other.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
        try:
            with pytest.raises(Retry):
                transcribe.run(str(identifier), 0)
        finally:
            other.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
    assert retry.call_args.kwargs["countdown"] == jobs.LOCK_BUSY_COUNTDOWN
    with SessionLocal() as db:
        assert db.get(VoiceJob, identifier).attempts == 0


def test_recovery_repairs_killed_processing_soon_but_not_waiting_queue():
    processing = queue_voice(100, "Alex", 100, 14, "audio")
    waiting = queue_voice(100, "Alex", 100, 15, "audio")
    lost = queue_voice(100, "Alex", 100, 16, "audio")
    with SessionLocal.begin() as db:
        db.get(VoiceJob, processing).status = "processing"
        db.get(VoiceJob, processing).updated_at = now() - timedelta(minutes=12)
        db.get(VoiceJob, waiting).updated_at = now() - timedelta(minutes=30)
        db.get(VoiceJob, lost).updated_at = now() - timedelta(minutes=61)
    assert recover_stale_jobs() == 2
    with SessionLocal() as db:
        recovered = {
            row.payload["job_id"]
            for row in db.scalars(select(Outbox).where(Outbox.dedupe_key.startswith("recover:")))
        }
    assert recovered == {str(processing), str(lost)}


def test_bad_outbox_row_is_isolated_then_quarantined(monkeypatch):
    with SessionLocal.begin() as db:
        outbox(db, "mystery", "bad-row", value=1)
    with SessionLocal.begin() as db:
        outbox(db, "notification", "good-row", chat_id=100, text="Hello")
    send = Mock()
    monkeypatch.setattr("app.dispatcher.celery.send_task", send)
    cache = redis.Redis.from_url(settings().redis_url)
    assert dispatch_once(cache) == 1
    assert send.call_count == 1
    for _ in range(OUTBOX_MAX_ATTEMPTS + 2):
        assert dispatch_once(cache) == 0
    cache.close()
    assert send.call_count == 1
    with SessionLocal() as db:
        bad = db.scalar(select(Outbox).where(Outbox.dedupe_key == "bad-row"))
        assert bad.delivered_at is None and bad.attempts == OUTBOX_MAX_ATTEMPTS
        assert bad.last_error == "KeyError"
        good = db.scalar(select(Outbox).where(Outbox.dedupe_key == "good-row"))
        assert good.delivered_at is not None and good.attempts == 0


def test_broker_outage_keeps_rows_already_published(monkeypatch):
    with SessionLocal.begin() as db:
        outbox(db, "notification", "first", chat_id=100, text="1")
    with SessionLocal.begin() as db:
        outbox(db, "notification", "second", chat_id=100, text="2")
    send = Mock(side_effect=[None, redis.ConnectionError("offline")])
    monkeypatch.setattr("app.dispatcher.celery.send_task", send)
    cache = redis.Redis.from_url(settings().redis_url)
    with pytest.raises(redis.ConnectionError):
        dispatch_once(cache)
    with SessionLocal() as db:
        first = db.scalar(select(Outbox).where(Outbox.dedupe_key == "first"))
        second = db.scalar(select(Outbox).where(Outbox.dedupe_key == "second"))
        assert first.delivered_at is not None
        assert second.delivered_at is None and second.attempts == 0
    send.side_effect = None
    assert dispatch_once(cache) == 1
    cache.close()


def test_periodic_failure_is_logged_not_raised():
    run_periodic("reminder_schedule", Mock(side_effect=RuntimeError("database offline")))


def personal_task(due_at, message_id):
    from app.domain import create_text_task

    identifier = create_text_task(100, "Alex", 100, message_id, f"Due {message_id}")
    with SessionLocal.begin() as db:
        db.get(Task, identifier).due_at = due_at
    return identifier


def test_reminder_scan_is_bounded_and_does_not_rescan_reminded_tasks():
    for number in range(8):
        personal_task(now() - timedelta(hours=number + 1), 300 + number)
    ancient = personal_task(now() - timedelta(days=8), 400)
    assert schedule_reminders() == 8
    statements = []
    listener = lambda *args: statements.append(args[2])  # noqa: E731
    event.listen(engine, "before_cursor_execute", listener)
    try:
        assert schedule_reminders() == 0
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    assert len([sql for sql in statements if not sql.startswith(("SAVEPOINT", "RELEASE"))]) <= 3
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(TaskReminder)) == 8
        assert db.scalar(select(TaskReminder).where(TaskReminder.task_id == ancient)) is None
        assert db.scalar(select(func.count()).select_from(Outbox).where(Outbox.kind == "reminder"))


def test_undelivered_reminder_is_rescheduled_a_bounded_number_of_times():
    personal_task(now() + timedelta(days=1), 500)
    assert schedule_reminders() == 1
    for expected in range(2, MAX_ATTEMPTS + 1):
        with SessionLocal.begin() as db:
            db.scalar(select(TaskReminder)).queued_at = now() - timedelta(minutes=31)
        assert schedule_reminders() == 1
        with SessionLocal() as db:
            reminder = db.scalar(select(TaskReminder))
            assert reminder.attempts == expected
            assert db.scalar(
                select(Outbox).where(Outbox.dedupe_key == f"reminder:{reminder.id}:{expected}")
            )
    with SessionLocal.begin() as db:
        db.scalar(select(TaskReminder)).queued_at = now() - timedelta(minutes=31)
    assert schedule_reminders() == 0
    with SessionLocal.begin() as db:
        reminder = db.scalar(select(TaskReminder))
        reminder.sent_at, reminder.attempts = now(), 1
        reminder.queued_at = now() - timedelta(hours=2)
    assert schedule_reminders() == 0


def mock_telegram(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(
        "app.transcription.httpx.Client",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )


@pytest.mark.parametrize("case", ["no_path", "file_404", "metadata_429"])
def test_download_classifies_telegram_failures(monkeypatch, tmp_path, case):
    def handler(request):
        if request.url.path.endswith("getFile"):
            if case == "metadata_429":
                return httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 42}})
            result = {"file_size": 10} if case == "no_path" else {"file_path": "voice/x.oga"}
            return httpx.Response(200, json={"ok": True, "result": result})
        return httpx.Response(404, json={"ok": False})

    mock_telegram(monkeypatch, handler)
    if case == "metadata_429":
        with pytest.raises(TelegramRetryAfter) as error:
            download_voice("file", tmp_path / "audio.ogg")
        assert error.value.seconds == 42
    else:
        with pytest.raises(PermanentVoiceError, match="could not retrieve"):
            download_voice("file", tmp_path / "audio.ogg")


def test_notification_honors_retry_after_and_sends_markup(monkeypatch):
    limited = httpx.Response(
        429,
        json={"ok": False, "parameters": {"retry_after": 77}},
        request=httpx.Request("POST", "https://api.telegram.org"),
    )
    monkeypatch.setattr("app.jobs.httpx.post", Mock(return_value=limited))
    retry = Mock(side_effect=Retry())
    monkeypatch.setattr(notify, "retry", retry)
    with pytest.raises(Retry):
        notify.run(100, "Hi")
    assert retry.call_args.kwargs["countdown"] == 77
    send = Mock(return_value=Mock(status_code=200))
    monkeypatch.setattr("app.jobs.httpx.post", send)
    notify.run(
        100,
        "<b>Hi</b>",
        retry_job_id="job",
        parse_mode="HTML",
        reply_markup={"inline_keyboard": [[{"text": "Open", "callback_data": "task:x"}]]},
    )
    payload = send.call_args.kwargs["json"]
    assert payload["parse_mode"] == "HTML"
    assert [row[0]["callback_data"] for row in payload["reply_markup"]["inline_keyboard"]] == [
        "retry:job",
        "task:x",
    ]


def test_typing_indicator_only_while_job_is_processing(monkeypatch):
    identifier = queue_voice(100, "Alex", 100, 17, "audio")
    send = Mock()
    monkeypatch.setattr("app.jobs.httpx.post", send)
    chat_action.run(100, job_id=str(identifier))
    assert not send.called
    with SessionLocal.begin() as db:
        db.get(VoiceJob, identifier).status = "processing"
    chat_action.run(100, job_id=str(identifier), recipient_id=str(uuid.uuid4()))
    assert not send.called
    chat_action.run(100, job_id=str(identifier))
    assert send.call_args.kwargs["json"] == {"chat_id": 100, "action": "typing"}
    assert send.call_args.args[0].endswith("/sendChatAction")


def test_failed_job_retry_rechecks_edit_permission(monkeypatch):
    from app.domain import create_text_task

    target = create_text_task(100, "Alex", 100, 18, "Original")
    identifier = queue_voice_edit(100, "Alex", 100, 19, "audio", target, "text")
    monkeypatch.setattr("app.jobs.download_voice", Mock(side_effect=PermanentVoiceError("Bad.")))
    transcribe.run(str(identifier), 0)
    assert not retry_voice(identifier, 200)
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.telegram_id == 100))
    ws.delete_task(owner, target)
    with pytest.raises(ws.WorkspaceError):
        retry_voice(identifier, 100)


def test_stale_temporary_audio_is_swept(tmp_path):
    old, fresh, other = tmp_path / "taskbot-old", tmp_path / "taskbot-new", tmp_path / "keep-me"
    for path in (old, fresh, other):
        path.mkdir()
        (path / "voice.ogg").write_bytes(b"x")
    stale = time.time() - 7200
    os.utime(old, (stale, stale))
    os.utime(other, (stale, stale))
    assert sweep_stale_temporary(str(tmp_path)) == 1
    assert not old.exists() and fresh.exists() and other.exists()


def test_migrations_refuse_lossy_downgrade_and_round_trip():
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    head = ScriptDirectory.from_config(config).get_current_head()
    engine.dispose()
    try:
        command.downgrade(config, "0012")
        command.upgrade(config, "head")
        with SessionLocal() as db:
            owner = db.scalar(select(User).where(User.telegram_id == 100))
            developer = db.scalar(select(User).where(User.telegram_id == 200))
        project = ws.create_project(owner, "Keep")
        ws.add_member(owner, project["id"], developer.email)
        ws.create_task(
            owner, "Two people", project_id=project["id"], assignee_ids=[owner.id, developer.id]
        )
        ws.archive_project(owner, project["id"], True)
        engine.dispose()
        with pytest.raises(RuntimeError, match="archived projects"):
            command.downgrade(config, "0013")
        ws.archive_project(owner, project["id"], False)
        engine.dispose()
        with pytest.raises(RuntimeError, match="one assignee"):
            command.downgrade(config, "0012")
    finally:
        engine.dispose()
        command.upgrade(config, "head")
    with SessionLocal() as db:
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == head
        assert db.scalar(select(func.count()).select_from(Task)) == 1
