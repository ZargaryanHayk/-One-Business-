from unittest.mock import Mock

import httpx
import pytest
from celery.exceptions import Retry
from sqlalchemy import delete, func, select

from app import workspace as ws
from app.db import SessionLocal
from app.domain import queue_voice, retry_voice
from app.jobs import notify, transcribe
from app.models import Outbox, ProjectMember, Task, TaskComment, User, VoiceJob
from app.transcription import PermanentVoiceError


def comment_target():
    with SessionLocal() as db:
        manager = db.scalar(select(User).where(User.telegram_id == 100))
        developer = db.scalar(select(User).where(User.telegram_id == 200))
    project = ws.create_project(manager, "Voice comments")
    ws.add_member(manager, project["id"], developer.email)
    task = ws.create_task(manager, "Review the design", project_id=project["id"])
    return manager, developer, project, task


def test_voice_comment_transcription_is_atomic_idempotent_and_live(monkeypatch):
    _, developer, _, task = comment_target()
    args = (200, "Jamie", 200, 77, "comment-audio")
    identifier = queue_voice(*args, comment_task_id=task["id"])
    assert queue_voice(*args, comment_task_id=task["id"]) == identifier
    # A repeated message arriving after the comment conversation ended retains its target.
    assert queue_voice(*args) == identifier
    transcript = "Փոփոխությունները պատրաստ են։ Ready for review."
    convert = Mock(return_value=transcript)
    monkeypatch.setattr("app.jobs.download_voice", Mock())
    monkeypatch.setattr("app.jobs.transcribe_audio", convert)
    with SessionLocal() as db:
        events_before = db.scalar(
            select(func.count()).select_from(Outbox).where(Outbox.kind == "event")
        )
    transcribe.run(str(identifier), 0)
    transcribe.run(str(identifier), 0)
    assert convert.call_count == 1
    with SessionLocal() as db:
        job = db.get(VoiceJob, identifier)
        assert job.status == "completed" and job.comment_task_id == task["id"]
        comment = db.scalar(select(TaskComment))
        assert comment.task_id == task["id"] and comment.author_id == developer.id
        assert comment.text == transcript and comment.message_id == 77
        assert db.scalar(select(func.count()).select_from(TaskComment)) == 1
        assert db.scalar(select(func.count()).select_from(Task)) == 1
        assert (
            db.scalar(select(func.count()).select_from(Outbox).where(Outbox.kind == "event"))
            == events_before + 2
        )
        notice = db.scalar(select(Outbox).where(Outbox.kind == "notification"))
        assert "voice comment" in notice.payload["text"]
        assert str(task["id"]) in notice.payload["text"]
    assert ws.comments(developer, task["id"])[0]["text"] == transcript
    monkeypatch.setattr("app.jobs.httpx.post", Mock(side_effect=httpx.ConnectError("offline")))
    monkeypatch.setattr(notify, "retry", Mock(side_effect=Retry()))
    with pytest.raises(Retry):
        notify.run(**notice.payload)
    transcribe.run(str(identifier), 0)
    assert convert.call_count == 1


@pytest.mark.parametrize("change", ["delete", "remove_member"])
def test_voice_comment_rechecks_access_after_queue_and_on_retry(monkeypatch, change):
    manager, _, project, task = comment_target()
    identifier = queue_voice(200, "Jamie", 200, 78, "audio", comment_task_id=task["id"])
    if change == "delete":
        ws.delete_task(manager, task["id"])
    else:
        with SessionLocal.begin() as db:
            db.execute(
                delete(ProjectMember).where(
                    ProjectMember.project_id == project["id"],
                    ProjectMember.user_id != manager.id,
                )
            )
    monkeypatch.setattr("app.jobs.download_voice", Mock())
    monkeypatch.setattr("app.jobs.transcribe_audio", Mock(return_value="Do not save this"))
    transcribe.run(str(identifier), 0)
    with SessionLocal() as db:
        assert db.get(VoiceJob, identifier).status == "failed"
        assert db.scalar(select(func.count()).select_from(TaskComment)) == 0
        assert db.scalar(select(func.count()).select_from(Task)) == 1
    with pytest.raises(ws.WorkspaceError):
        retry_voice(identifier, 200)


def test_cannot_queue_voice_comment_on_another_users_personal_task():
    with SessionLocal() as db:
        manager = db.scalar(select(User).where(User.telegram_id == 100))
    task = ws.create_task(manager, "Private")
    with pytest.raises(ws.WorkspaceError):
        queue_voice(200, "Jamie", 200, 79, "audio", comment_task_id=task["id"])
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(VoiceJob)) == 0


@pytest.mark.parametrize("failure", ["silence", "corrupt"])
def test_failed_voice_comments_can_retry_without_creating_tasks(monkeypatch, failure):
    _, _, _, task = comment_target()
    identifier = queue_voice(200, "Jamie", 200, 80, "audio", comment_task_id=task["id"])
    monkeypatch.setattr("app.jobs.download_voice", Mock())
    convert = Mock(
        side_effect=PermanentVoiceError(
            "No speech detected." if failure == "silence" else "Could not decode this voice note."
        )
    )
    monkeypatch.setattr("app.jobs.transcribe_audio", convert)
    transcribe.run(str(identifier), 0)
    with SessionLocal() as db:
        assert db.get(VoiceJob, identifier).status == "failed"
        assert db.scalar(select(func.count()).select_from(TaskComment)) == 0
        assert db.scalar(select(func.count()).select_from(Task)) == 1
        notice = db.scalar(select(Outbox).where(Outbox.kind == "notification"))
        assert "voice comment" in notice.payload["text"]
    assert not retry_voice(identifier, 100)
    assert retry_voice(identifier, 200)
    convert.side_effect = None
    convert.return_value = "Retry succeeded"
    transcribe.run(str(identifier), 0)
    transcribe.run(str(identifier), 1)
    with SessionLocal() as db:
        assert db.get(VoiceJob, identifier).status == "completed"
        assert db.scalar(select(TaskComment)).text == "Retry succeeded"
        assert db.scalar(select(func.count()).select_from(Task)) == 1
