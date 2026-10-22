import uuid
from datetime import timedelta
from unittest.mock import Mock

import httpx
import pytest
import redis
from sqlalchemy import func, select

from app import workspace as ws
from app.auth import hasher
from app.config import settings
from app.db import SessionLocal
from app.dispatcher import dispatch_once
from app.domain import queue_voice, telegram_tasks
from app.jobs import transcribe
from app.models import Outbox, Task, TaskReminder, TelegramSession, User, now
from app.reminders import schedule_reminders, send_reminder


def setup_project(client, login, due=None):
    pm = login()
    with SessionLocal() as db:
        developer = db.scalar(select(User).where(User.telegram_id == 200))
    project_response = client.post("/api/projects", json={"name": "Launch"})
    assert project_response.status_code == 201
    project = project_response.json()
    assert (
        client.post(
            f"/api/projects/{project['id']}/members", json={"email": developer.email}
        ).status_code
        == 200
    )
    response = client.post(
        "/api/tasks",
        json={
            "text": "Build the launch flow",
            "project_id": project["id"],
            "assignee_id": str(developer.id),
            "due_at": due.isoformat() if due else None,
        },
    )
    assert response.status_code == 201, response.text
    return pm, developer, project, response.json()


def test_manager_developer_project_assignment_and_my_tasks(client, login):
    pm, developer, project, task = setup_project(client, login)
    assert client.get("/api/me").json()["role"] == "project_manager"
    assert client.get("/api/tasks", params={"mine": True}).json() == []
    assert task["assignee"]["id"] == str(developer.id)
    assert task["can_manage"] and task["can_move"]
    login(200)
    assert client.get("/api/me").json()["role"] == "developer"
    assert client.get("/api/projects").json()[0]["id"] == project["id"]
    assert client.get("/api/tasks", params={"mine": True}).json()[0]["id"] == task["id"]
    assert telegram_tasks(200)[0][0].id == uuid.UUID(task["id"])
    viewed = client.get(f"/api/tasks/{task['id']}").json()
    assert viewed["can_move"] and not viewed["can_manage"]
    assert client.patch(f"/api/tasks/{task['id']}", json={"status": "completed"}).status_code == 200
    for change in (
        {"text": "Changed"},
        {"title": "Unauthorized headline"},
        {"due_at": now().isoformat()},
        {"assignee_id": str(pm.id)},
    ):
        assert client.patch(f"/api/tasks/{task['id']}", json=change).status_code == 403
    assert client.delete(f"/api/tasks/{task['id']}").status_code == 403
    assert client.post("/api/projects", json={"name": "Unauthorized"}).status_code == 403
    assert (
        client.post("/api/tasks", json={"text": "No", "project_id": project["id"]}).status_code
        == 403
    )
    assert (
        client.post(f"/api/projects/{project['id']}/members", json={"email": pm.email}).status_code
        == 403
    )


def test_remove_project_member_and_permissions(client, login):
    pm, developer, project, task = setup_project(client, login)
    login(200)
    assert client.delete(f"/api/projects/{project['id']}/members/{pm.id}").status_code == 403
    login()
    assert client.delete(f"/api/projects/{project['id']}/members/{pm.id}").status_code == 422
    res = client.delete(f"/api/projects/{project['id']}/members/{developer.id}")
    assert res.status_code == 204
    members = client.get(f"/api/projects/{project['id']}/members").json()
    assert all(m["id"] != str(developer.id) for m in members)
    updated_task = client.get(f"/api/tasks/{task['id']}").json()
    assert updated_task["assignee"] is None


def test_unassigned_members_only_get_sidebar_project_after_assignment(client, login):
    login()
    project = client.post("/api/projects", json={"name": "Upcoming"}).json()
    client.post(f"/api/projects/{project['id']}/members", json={"email": "test2@hire.me"})
    login(200)
    assert client.get("/api/projects").json() == []
    assert client.get(f"/api/projects/{project['id']}/members").status_code == 200


def test_outsider_cannot_read_change_or_comment_project_tasks(client, login):
    _, _, project, task = setup_project(client, login)
    with SessionLocal.begin() as db:
        outsider = User(
            email="outside@example.test", password_hash=hasher.hash("outside-pass"), name="Outsider"
        )
        db.add(outsider)
    client.post(
        "/api/auth/login", json={"email": "outside@example.test", "password": "outside-pass"}
    )
    assert client.get("/api/projects").json() == []
    assert client.get("/api/tasks").json() == []
    assert client.get(f"/api/projects/{project['id']}/members").status_code == 404
    for path in (f"/api/tasks/{task['id']}", f"/api/tasks/{task['id']}/comments"):
        assert client.get(path).status_code == 404
    assert client.patch(f"/api/tasks/{task['id']}", json={"status": "completed"}).status_code == 404
    assert (
        client.post(f"/api/tasks/{task['id']}/comments", json={"text": "No access"}).status_code
        == 404
    )


def test_project_comments_sync_and_reject_empty_or_foreign_updates(client, login):
    _, _, _, task = setup_project(client, login)
    login(200)
    result = client.post(f"/api/tasks/{task['id']}/comments", json={"text": "Ready for review!"})
    assert result.status_code == 201
    assert result.json()["author"]["email"] == "test2@hire.me"
    assert (
        client.post(f"/api/tasks/{task['id']}/comments", json={"text": " \n "}).status_code == 422
    )
    login()
    assert client.get(f"/api/tasks/{task['id']}/comments").json()[0]["text"] == "Ready for review!"
    assert (
        client.post(
            f"/api/tasks/{task['id']}/comments",
            json={"text": "No origin"},
            headers={"Origin": "null"},
        ).status_code
        == 403
    )


def test_assignment_requires_project_membership_and_aware_deadline(client, login):
    login()
    with SessionLocal() as db:
        developer = db.scalar(select(User).where(User.telegram_id == 200))
    project = client.post("/api/projects", json={"name": "Private"}).json()
    assert (
        client.post(
            "/api/tasks",
            json={
                "text": "No member",
                "project_id": project["id"],
                "assignee_id": str(developer.id),
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/tasks", json={"text": "Bad date", "due_at": "2026-10-22T12:00:00"}
        ).status_code
        == 422
    )
    assert client.post("/api/projects", json={"name": "   "}).status_code == 422


def test_due_soon_includes_overdue_excludes_completed_and_more_than_four_days(client, login):
    _, _, _, task = setup_project(client, login, now() + timedelta(days=3))
    login(200)
    assert len(client.get("/api/tasks", params={"mine": True, "due_soon": True}).json()) == 1
    client.patch(f"/api/tasks/{task['id']}", json={"status": "completed"})
    assert client.get("/api/tasks", params={"due_soon": True}).json() == []
    login()
    client.patch(
        f"/api/tasks/{task['id']}",
        json={"status": "pending", "due_at": (now() - timedelta(days=1)).isoformat()},
    )
    assert len(client.get("/api/tasks", params={"due_soon": True}).json()) == 1
    client.patch(
        f"/api/tasks/{task['id']}", json={"due_at": (now() + timedelta(days=5)).isoformat()}
    )
    assert client.get("/api/tasks", params={"due_soon": True}).json() == []


def test_reminders_schedule_once_for_assignee_and_delivery_is_idempotent(
    client, login, monkeypatch
):
    _, _, _, task = setup_project(client, login, now() + timedelta(days=3))
    assert schedule_reminders() == 1
    assert schedule_reminders() == 0
    with SessionLocal() as db:
        identifier = str(db.scalar(select(TaskReminder.id)))
    send = Mock(return_value=Mock(status_code=200))
    monkeypatch.setattr("app.reminders.httpx.post", send)
    send_reminder(identifier)
    send_reminder(identifier)
    assert send.call_count == 1
    assert send.call_args.kwargs["json"]["chat_id"] == 200
    assert "Deadline reminder" in send.call_args.kwargs["json"]["text"]
    assert (
        send.call_args.kwargs["json"]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        == f"task:{task['id']}"
    )
    assert schedule_reminders() == 0


@pytest.mark.parametrize(
    "change", ["completed", "deleted", "rescheduled", "reassigned", "logged_out"]
)
def test_reminder_rechecks_task_and_recipient_before_sending(client, login, monkeypatch, change):
    pm, _, _, task = setup_project(client, login, now() + timedelta(days=1))
    schedule_reminders()
    with SessionLocal() as db:
        identifier = str(db.scalar(select(TaskReminder.id)))
    if change == "deleted":
        client.delete(f"/api/tasks/{task['id']}")
    elif change == "logged_out":
        with SessionLocal.begin() as db:
            db.delete(db.get(TelegramSession, 200))
    else:
        fields = {
            "completed": {"status": "completed"},
            "rescheduled": {"due_at": (now() + timedelta(days=8)).isoformat()},
            "reassigned": {"assignee_id": str(pm.id)},
        }[change]
        client.patch(f"/api/tasks/{task['id']}", json=fields)
    send = Mock()
    monkeypatch.setattr("app.reminders.httpx.post", send)
    send_reminder(identifier)
    assert not send.called


def test_reminder_retries_can_succeed_and_new_deadline_has_its_own_reminder(
    client, login, monkeypatch
):
    _, _, _, task = setup_project(client, login, now() + timedelta(days=2))
    schedule_reminders()
    with SessionLocal() as db:
        identifier = str(db.scalar(select(TaskReminder.id)))
    monkeypatch.setattr("app.reminders.httpx.post", Mock(side_effect=httpx.ConnectError("offline")))
    with pytest.raises(httpx.ConnectError):
        send_reminder(identifier)
    with SessionLocal() as db:
        assert db.scalar(select(TaskReminder)).sent_at is None
    send = Mock(return_value=Mock(status_code=200))
    monkeypatch.setattr("app.reminders.httpx.post", send)
    send_reminder(identifier)
    client.patch(
        f"/api/tasks/{task['id']}", json={"due_at": (now() + timedelta(days=3)).isoformat()}
    )
    assert schedule_reminders() == 1


def test_no_reminder_without_active_telegram_and_completed_tasks(client, login):
    _, _, _, task = setup_project(client, login, now() + timedelta(days=1))
    with SessionLocal.begin() as db:
        db.get(TelegramSession, 200).expires_at = now() - timedelta(seconds=1)
    assert schedule_reminders() == 0
    client.patch(f"/api/tasks/{task['id']}", json={"status": "completed"})
    assert schedule_reminders() == 0


def test_project_events_fan_out_to_members_only(client, login, monkeypatch):
    pm, developer, _, _ = setup_project(client, login)
    cache = redis.Redis.from_url(settings().redis_url)
    with cache.pubsub() as stream:
        stream.subscribe(f"events:{pm.id}", f"events:{developer.id}")
        stream.get_message(timeout=1)
        stream.get_message(timeout=1)
        monkeypatch.setattr("app.dispatcher.celery.send_task", Mock())
        dispatch_once(cache)
        channels = set()
        while message := stream.get_message(timeout=0.2):
            channels.add(message["channel"].decode())
        assert channels == {f"events:{pm.id}", f"events:{developer.id}"}
    cache.close()


def test_project_voice_task_retains_assignment_and_deadline(client, login, monkeypatch):
    pm, developer, project, _ = setup_project(client, login)
    deadline = now() + timedelta(days=2)
    identifier = queue_voice(
        100, "Alex", 100, 100, "fixture", uuid.UUID(project["id"]), developer.id, deadline
    )
    monkeypatch.setattr("app.jobs.download_voice", Mock())
    monkeypatch.setattr("app.jobs.transcribe_audio", Mock(return_value="Voice project task"))
    transcribe.run(str(identifier), 0)
    with SessionLocal() as db:
        task = db.scalar(select(Task).where(Task.source == "voice"))
        assert task.user_id == pm.id and task.assignee_id == developer.id
        assert task.project_id == uuid.UUID(project["id"]) and task.due_at == deadline
        assert (
            db.scalar(select(func.count()).select_from(Outbox).where(Outbox.kind == "assignment"))
            == 2
        )


def test_admin_can_manage_projects_but_cannot_read_others_personal_tasks(client, login):
    _, _, project, _ = setup_project(client, login)
    login(200)
    personal = client.post("/api/tasks", json={"text": "Personal"}).json()
    with SessionLocal.begin() as db:
        db.scalar(select(User).where(User.telegram_id == 100)).role = "admin"
    admin = login()
    assert ws.list_projects(admin)[0]["can_manage"]
    assert client.get(f"/api/tasks/{personal['id']}").status_code == 404
    assert (
        client.post(
            f"/api/projects/{project['id']}/members", json={"email": "test1@hire.me"}
        ).status_code
        == 200
    )


def test_reminder_outbox_redis_and_notification_worker(client, login, monkeypatch):
    import time

    from celery.contrib.testing.worker import start_worker

    from app.celery_app import celery

    setup_project(client, login, now() + timedelta(days=2))
    assert schedule_reminders() == 1
    send = Mock(return_value=Mock(status_code=200))
    monkeypatch.setattr("app.reminders.httpx.post", send)
    cache = redis.Redis.from_url(settings().redis_url)
    with start_worker(celery, queues=["notifications"], pool="solo", perform_ping_check=False):
        dispatch_once(cache)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            with SessionLocal() as db:
                reminder = db.scalar(select(TaskReminder))
                if reminder.sent_at is not None:
                    break
            time.sleep(0.1)
        else:
            pytest.fail("Reminder did not pass through Redis and the notification worker")
        assert schedule_reminders() == 0
        reminder_calls = [
            call
            for call in send.call_args_list
            if "Deadline reminder" in call.kwargs["json"]["text"]
        ]
        assert len(reminder_calls) == 1
        assert reminder_calls[0].kwargs["json"]["chat_id"] == 200
    cache.close()
