import shutil
import time
from pathlib import Path
from unittest.mock import Mock

import pytest
import redis
from celery.contrib.testing.worker import start_worker
from sqlalchemy import select

from app.celery_app import celery
from app.config import settings
from app.db import SessionLocal
from app.dispatcher import dispatch_once
from app.domain import queue_voice
from app.models import Outbox, Task, TaskComment, User, VoiceJob, now
from app.workspace import create_task


@pytest.mark.parametrize(
    "actual_transcription", [False, pytest.param(True, marks=pytest.mark.transcription)]
)
@pytest.mark.parametrize("as_comment", [False, True])
def test_redis_worker_pipeline(monkeypatch, actual_transcription, as_comment):
    fixture = Path(__file__).parent / "fixtures" / "task.wav"
    monkeypatch.setattr(
        "app.jobs.download_voice", lambda file_id, target: shutil.copy(fixture, target)
    )
    if not actual_transcription:
        monkeypatch.setattr(
            "app.jobs.transcribe_audio", Mock(return_value="Review the project plan.")
        )
    send = Mock(return_value=Mock(status_code=200))
    monkeypatch.setattr("app.jobs.httpx.post", send)
    cache = redis.Redis.from_url(settings().redis_url)
    target_id = None
    if as_comment:
        with SessionLocal() as db:
            owner = db.scalar(select(User).where(User.telegram_id == 100))
        target_id = create_task(owner, "A task to comment on")["id"]
        with SessionLocal.begin() as db:
            for event in db.scalars(select(Outbox)):
                event.delivered_at = now()
    identifier = queue_voice(100, "Alex", 100, 1, "fixture", comment_task_id=target_id)
    with start_worker(
        celery,
        queues=["transcription", "notifications"],
        pool="solo",
        perform_ping_check=False,
        shutdown_timeout=20,
    ):
        assert dispatch_once(cache) == 1
        deadline = time.monotonic() + (180 if actual_transcription else 15)
        while time.monotonic() < deadline:
            with SessionLocal() as db:
                record = db.get(VoiceJob, identifier)
                if record.status == "completed":
                    if as_comment:
                        comment = db.scalar(select(TaskComment))
                        assert comment.task_id == target_id
                        assert "review" in comment.text.lower()
                        assert db.scalar(select(Task)).text == "A task to comment on"
                    else:
                        task = db.scalar(select(Task))
                        assert "review" in task.text.lower()
                    break
                assert record.status != "failed", record.error
            time.sleep(0.1)
        else:
            pytest.fail("Worker did not complete the queued transcription")
        dispatch_once(cache)
        deadline = time.monotonic() + 10
        while not send.called and time.monotonic() < deadline:
            time.sleep(0.1)
        assert send.called
