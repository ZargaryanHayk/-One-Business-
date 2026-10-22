from datetime import timedelta
from unittest.mock import Mock

import httpx
import pytest
import redis
from celery.exceptions import Retry
from sqlalchemy import func, select

from app.config import settings
from app.db import SessionLocal
from app.dispatcher import dispatch_once, recover_stale_jobs
from app.domain import queue_voice, retry_voice
from app.jobs import notify, transcribe
from app.logging import JsonFormatter
from app.models import Outbox, Task, VoiceJob, now
from app.transcription import PermanentVoiceError


def job():
    return queue_voice(100, "Alex", 100, 1, "fake-telegram-file")


def test_voice_job_idempotency_and_transcription(monkeypatch):
    identifier = job()
    assert job() == identifier
    download = Mock()
    convert = Mock(return_value="Remember to review the proposal.")
    monkeypatch.setattr("app.jobs.download_voice", download)
    monkeypatch.setattr("app.jobs.transcribe_audio", convert)
    transcribe.run(str(identifier), 0)
    transcribe.run(str(identifier), 0)
    assert convert.call_count == 1
    with SessionLocal() as db:
        assert db.get(VoiceJob, identifier).status == "completed"
        tasks = list(db.scalars(select(Task)))
        assert len(tasks) == 1
        assert tasks[0].source == "voice"
        assert tasks[0].text == "Remember to review the proposal."
        assert len(list(db.scalars(select(Outbox).where(Outbox.kind == "notification")))) == 1


def test_entered_voice_task_title_survives_failure_retry_and_duplicate_delivery(monkeypatch):
    identifier = queue_voice(100, "Alex", 100, 92, "fixture", title="My chosen headline")
    monkeypatch.setattr("app.jobs.download_voice", Mock())
    convert = Mock(side_effect=PermanentVoiceError("No speech detected."))
    monkeypatch.setattr("app.jobs.transcribe_audio", convert)
    transcribe.run(str(identifier), 0)
    assert retry_voice(identifier, 100)
    convert.side_effect = None
    convert.return_value = "This is the complete spoken description."
    transcribe.run(str(identifier), 0)
    transcribe.run(str(identifier), 1)
    transcribe.run(str(identifier), 1)
    with SessionLocal() as db:
        task = db.scalar(select(Task))
        assert task.title == "My chosen headline"
        assert task.text == "This is the complete spoken description."
        assert db.get(VoiceJob, identifier).task_title == task.title
        assert db.scalar(select(func.count()).select_from(Task)) == 1


@pytest.mark.parametrize(
    "message", ["No speech detected.", "Invalid audio.", "Voice note too long."]
)
def test_permanent_failure_and_owner_retry(monkeypatch, message):
    identifier = job()
    monkeypatch.setattr("app.jobs.download_voice", Mock())
    monkeypatch.setattr("app.jobs.transcribe_audio", Mock(side_effect=PermanentVoiceError(message)))
    transcribe.run(str(identifier), 0)
    assert not retry_voice(identifier, 200)
    with SessionLocal() as db:
        assert db.get(VoiceJob, identifier).status == "failed"
        assert db.scalar(select(func.count()).select_from(Task)) == 0
    assert retry_voice(identifier, 100)
    assert not retry_voice(identifier, 100)
    transcribe.run(str(identifier), 0)  # old generation must be ignored
    with SessionLocal() as db:
        assert db.get(VoiceJob, identifier).status == "queued"
        assert db.get(VoiceJob, identifier).generation == 1


def test_transient_failure_is_bounded(monkeypatch):
    identifier = job()
    monkeypatch.setattr("app.jobs.download_voice", Mock(side_effect=httpx.ConnectError("offline")))
    monkeypatch.setattr(transcribe, "retry", Mock(side_effect=Retry()))
    for _ in range(3):
        with pytest.raises(Retry):
            transcribe.run(str(identifier), 0)
    transcribe.run(str(identifier), 0)
    with SessionLocal() as db:
        record = db.get(VoiceJob, identifier)
        assert record.status == "failed" and record.attempts == 4


def test_worker_loss_attempt_budget(monkeypatch):
    identifier = job()
    with SessionLocal.begin() as db:
        record = db.get(VoiceJob, identifier)
        record.status, record.attempts = "processing", 4
    convert = Mock()
    monkeypatch.setattr("app.jobs.transcribe_audio", convert)
    transcribe.run(str(identifier), 0)
    assert not convert.called
    with SessionLocal() as db:
        assert db.get(VoiceJob, identifier).status == "failed"


def test_outbox_survives_broker_failure(monkeypatch):
    job()
    cache = redis.Redis.from_url(settings().redis_url)
    send = Mock(side_effect=redis.ConnectionError("offline"))
    monkeypatch.setattr("app.dispatcher.celery.send_task", send)
    with pytest.raises(redis.ConnectionError):
        dispatch_once(cache)
    with SessionLocal() as db:
        assert db.scalar(select(Outbox)).delivered_at is None
    send.side_effect = None
    assert dispatch_once(cache) == 1
    assert dispatch_once(cache) == 0


def test_notification_failure_does_not_change_tasks(monkeypatch):
    identifier = job()
    monkeypatch.setattr("app.jobs.download_voice", Mock())
    monkeypatch.setattr("app.jobs.transcribe_audio", Mock(return_value="Ship the project"))
    transcribe.run(str(identifier), 0)
    monkeypatch.setattr("app.jobs.httpx.post", Mock(side_effect=httpx.ConnectError("offline")))
    monkeypatch.setattr(notify, "retry", Mock(side_effect=Retry()))
    with pytest.raises(Retry):
        notify.run(100, "Task created")
    with SessionLocal() as db:
        assert db.get(VoiceJob, identifier).status == "completed"
        assert db.scalar(select(func.count()).select_from(Task)) == 1


@pytest.mark.parametrize(
    ("status", "description", "reason"),
    [
        (
            400,
            "Bad Request: inline keyboard button URL 'secret-editor-link' is invalid",
            "invalid_button_url",
        ),
        (400, "Bad Request: can't parse entities: private-transcript", "request_rejected"),
        (403, "Forbidden: bot was blocked by the user", "request_rejected"),
    ],
)
def test_notification_rejection_logs_safe_diagnostics(
    monkeypatch, caplog, status, description, reason
):
    response = httpx.Response(status, json={"ok": False, "description": description})
    send = Mock(return_value=response)
    monkeypatch.setattr("app.jobs.httpx.post", send)
    notify.run(100, "private-transcript")
    assert send.call_count == 1
    record = next(item for item in caplog.records if item.message == "notification_rejected")
    formatted = JsonFormatter().format(record)
    assert record.error_type == f"TelegramHTTP{status}"
    assert record.rejection_reason == reason
    assert reason in formatted
    assert "secret-editor-link" not in formatted
    assert "private-transcript" not in formatted


def test_stale_jobs_recovered_without_resetting_attempt_budget():
    identifier = job()
    with SessionLocal.begin() as db:
        record = db.get(VoiceJob, identifier)
        record.status, record.attempts = "processing", 2
        record.updated_at = now() - timedelta(minutes=21)
    assert recover_stale_jobs() == 1
    assert recover_stale_jobs() == 0
    with SessionLocal() as db:
        assert db.get(VoiceJob, identifier).status == "queued"
        assert db.get(VoiceJob, identifier).attempts == 2
