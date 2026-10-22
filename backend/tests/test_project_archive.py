import uuid
from datetime import timedelta
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.domain import telegram_tasks
from app.models import Outbox, Project, Task, TaskReminder, now
from app.reminders import schedule_reminders, send_assignment, send_reminder
from tests.test_projects import setup_project


def test_project_archive_preserves_tasks_comments_assignments_and_statuses(client, login):
    pm, developer, project, task = setup_project(client, login)
    path = f"/api/projects/{project['id']}"
    task_path = f"/api/tasks/{task['id']}"
    client.post(f"{task_path}/comments", json={"text": "Keep the discussion"})
    completed = client.post(
        "/api/tasks",
        json={
            "text": "Already archived",
            "project_id": project["id"],
            "status": "completed",
            "assignee_ids": [str(pm.id), str(developer.id)],
        },
    ).json()
    client.patch(f"/api/tasks/{completed['id']}", json={"archived": True})
    with SessionLocal() as db:
        previous_events = set(db.scalars(select(Outbox.id)))
    response = client.patch(path, json={"archived": True})
    assert response.status_code == 200, response.text
    archived_at = response.json()["archived_at"]
    assert archived_at
    assert client.patch(path, json={"archived": True}).json()["archived_at"] == archived_at
    assert client.get("/api/projects").json() == []
    assert client.get("/api/projects?archived=true").json()[0]["id"] == project["id"]
    assert client.get("/api/tasks").json() == []
    assert client.get("/api/tasks?archived=true").json() == []
    read = client.get(task_path).json()
    assert read["project_archived"]
    assert not read["can_move"] and not read["can_manage"] and not read["can_archive"]
    assert client.get(f"{task_path}/comments").json()[0]["text"] == "Keep the discussion"
    assert client.get(f"/api/tasks?project_id={project['id']}").json()[0]["id"] == task["id"]
    with SessionLocal() as db:
        saved = db.get(Task, uuid.UUID(task["id"]))
        assert saved.status == "pending" and saved.deleted_at is None and saved.archived_at is None
        assert db.get(Task, uuid.UUID(completed["id"])).archived_at is not None
        assert db.get(Project, uuid.UUID(project["id"])).archived_at is not None
        recipients = {
            row.payload["user_id"]
            for row in db.scalars(select(Outbox))
            if row.kind == "event" and row.id not in previous_events
        }
        assert {str(pm.id), str(developer.id)} <= recipients
    login(200)
    assert client.get("/api/projects").json() == []
    assert client.get("/api/projects?archived=true").json()[0]["id"] == project["id"]
    assert telegram_tasks(200) == ([], False)
    login()
    assert client.patch(path, json={"archived": False}).json()["archived_at"] is None
    assert client.get("/api/projects").json()[0]["id"] == project["id"]
    assert client.get("/api/projects?archived=true").json() == []
    assert client.get(task_path).json()["can_move"]
    login(200)
    assert str(telegram_tasks(200)[0][0].id) == task["id"]
    assert (
        client.get("/api/tasks?mine=true&archived=true").json()[0]["assignee_ids"]
        == completed["assignee_ids"]
    )


def test_archived_projects_reject_changes_until_restored(client, login):
    _, developer, project, task = setup_project(client, login)
    path = f"/api/projects/{project['id']}"
    task_path = f"/api/tasks/{task['id']}"
    client.patch(path, json={"archived": True})
    for response in (
        client.post("/api/tasks", json={"text": "New work", "project_id": project["id"]}),
        client.patch(task_path, json={"status": "completed"}),
        client.patch(task_path, json={"text": "Changed"}),
        client.delete(task_path),
        client.post(f"{task_path}/comments", json={"text": "New discussion"}),
        client.post(f"{path}/members", json={"email": developer.email}),
        client.delete(f"{path}/members/{developer.id}"),
    ):
        assert response.status_code == 409, response.text
    login(200)
    assert client.patch(task_path, json={"status": "completed"}).status_code == 409
    login()
    client.patch(path, json={"archived": False})
    assert client.patch(task_path, json={"status": "completed"}).status_code == 200


@pytest.mark.parametrize("archived", [True, False])
def test_only_project_managers_can_archive_or_restore(client, login, archived):
    _, _, project, _ = setup_project(client, login)
    path = f"/api/projects/{project['id']}"
    if not archived:
        client.patch(path, json={"archived": True})
    assert (
        client.patch(path, json={"archived": archived}, headers={"Origin": "null"}).status_code
        == 403
    )
    assert client.patch(path, json={"archived": None}).status_code == 422
    login(200)
    assert client.patch(path, json={"archived": archived}).status_code == 403
    assert (
        client.patch(f"/api/projects/{uuid.uuid4()}", json={"archived": archived}).status_code
        == 404
    )
    client.cookies.clear()
    assert client.patch(path, json={"archived": archived}).status_code == 401


def test_archiving_pauses_reminders_and_queued_assignment_delivery(client, login, monkeypatch):
    _, developer, project, task = setup_project(client, login, now() + timedelta(days=2))
    path = f"/api/projects/{project['id']}"
    assert schedule_reminders() == 1
    with SessionLocal() as db:
        identifier = str(db.scalar(select(TaskReminder.id)))
    client.patch(path, json={"archived": True})
    assert schedule_reminders() == 0
    send = Mock(return_value=Mock(status_code=200))
    monkeypatch.setattr("app.reminders.httpx.post", send)
    send_reminder(identifier)
    send_assignment(task["id"], str(developer.id))
    assert not send.called
    client.patch(path, json={"archived": False})
    with SessionLocal() as db:
        resumed = list(
            db.scalars(
                select(Outbox).where(
                    Outbox.dedupe_key.startswith(f"project-reminder:{identifier}:")
                )
            )
        )
        assert len(resumed) == 1 and resumed[0].payload["reminder_id"] == identifier
    client.patch(path, json={"archived": False})
    with SessionLocal() as db:
        assert (
            len(
                list(
                    db.scalars(
                        select(Outbox).where(
                            Outbox.dedupe_key.startswith(f"project-reminder:{identifier}:")
                        )
                    )
                )
            )
            == 1
        )
    send_reminder(identifier)
    assert send.call_count == 1
