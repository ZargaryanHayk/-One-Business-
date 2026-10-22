import uuid

import pytest
from sqlalchemy import select

from app import workspace as ws
from app.db import SessionLocal
from app.domain import telegram_tasks
from app.models import Outbox, Task, User


@pytest.mark.parametrize("source", ["dashboard", "text", "voice"])
def test_archive_preserves_completed_task_and_comments_and_restores(client, login, source):
    owner = login()
    task = ws.create_task(
        owner,
        text="Keep the complete description",
        title="Finished work",
        status="completed",
        source=source,
        chat_id=100 if source != "dashboard" else None,
        message_id=42 if source != "dashboard" else None,
    )
    identifier = str(task["id"])
    client.post(f"/api/tasks/{identifier}/comments", json={"text": "Keep this discussion"})
    with SessionLocal() as db:
        previous_events = set(db.scalars(select(Outbox.id)))
    response = client.patch(f"/api/tasks/{identifier}", json={"archived": True})
    assert response.status_code == 200, response.text
    archived = response.json()
    assert archived["archived_at"] and archived["status"] == "completed"
    assert not archived["can_move"]
    for params in ({}, {"mine": True}, {"due_soon": True}):
        assert client.get("/api/tasks", params=params).json() == []
    assert client.get("/api/tasks", params={"archived": True}).json()[0]["id"] == identifier
    assert client.get(f"/api/tasks/{identifier}").json()["text"] == task["text"]
    assert (
        client.get(f"/api/tasks/{identifier}/comments").json()[0]["text"] == "Keep this discussion"
    )
    assert telegram_tasks(100) == ([], False)
    assert client.patch(f"/api/tasks/{identifier}", json={"status": "pending"}).status_code == 409
    assert (
        client.post(f"/api/tasks/{identifier}/comments", json={"text": "Later"}).status_code == 409
    )
    with SessionLocal() as db:
        saved = db.get(Task, uuid.UUID(identifier))
        assert saved.archived_at is not None and saved.deleted_at is None
        new_events = [row for row in db.scalars(select(Outbox)) if row.id not in previous_events]
        assert any(
            row.kind == "event" and row.payload["user_id"] == str(owner.id) for row in new_events
        )
    restored = client.patch(f"/api/tasks/{identifier}", json={"archived": False})
    assert restored.status_code == 200
    assert restored.json()["archived_at"] is None
    assert restored.json()["status"] == "completed"
    assert client.get("/api/tasks").json()[0]["id"] == identifier
    assert client.get("/api/tasks", params={"archived": True}).json() == []
    assert str(telegram_tasks(100)[0][0].id) == identifier


@pytest.mark.parametrize("status", ["pending", "in_progress"])
def test_only_completed_tasks_can_be_archived(client, login, status):
    login()
    task = client.post("/api/tasks", json={"text": "Still working", "status": status}).json()
    response = client.patch(f"/api/tasks/{task['id']}", json={"archived": True})
    assert response.status_code == 422
    assert client.get("/api/tasks").json()[0]["archived_at"] is None


def test_archive_requires_authentication_origin_and_task_ownership(client, login):
    owner = login()
    task = ws.create_task(owner, text="Private completed work", status="completed")
    path = f"/api/tasks/{task['id']}"
    assert (
        client.patch(path, json={"archived": True}, headers={"Origin": "null"}).status_code == 403
    )
    for body in ({"archived": None}, {"archived": True, "status": "completed"}):
        assert client.patch(path, json=body).status_code == 422
    login(200)
    assert client.patch(path, json={"archived": True}).status_code == 404
    assert client.get("/api/tasks", params={"archived": True}).json() == []
    client.cookies.clear()
    assert client.patch(path, json={"archived": True}).status_code == 401


def test_project_archive_permissions_events_and_scope(client, login):
    owner = login()
    project = client.post("/api/projects", json={"name": "Archive project"}).json()
    client.post(f"/api/projects/{project['id']}/members", json={"email": "test2@hire.me"})
    with SessionLocal() as db:
        developer = db.scalar(select(User).where(User.telegram_id == 200))
    task = client.post(
        "/api/tasks",
        json={
            "text": "Completed project work",
            "status": "completed",
            "project_id": project["id"],
            "assignee_id": str(developer.id),
        },
    ).json()
    path = f"/api/tasks/{task['id']}"
    login(200)
    assignee_view = client.get(path).json()
    assert assignee_view["can_archive"] and not assignee_view["can_manage"]
    with SessionLocal() as db:
        previous_events = set(db.scalars(select(Outbox.id)))
    assert client.patch(path, json={"archived": True}).status_code == 200
    with SessionLocal() as db:
        recipients = {
            row.payload["user_id"]
            for row in db.scalars(select(Outbox))
            if row.id not in previous_events and row.kind == "event"
        }
    assert {str(owner.id), str(developer.id)} <= recipients
    login(200)
    assert client.get("/api/tasks", params={"project_id": project["id"]}).json() == []
    assert client.get("/api/tasks", params={"archived": True}).json()[0]["id"] == task["id"]
    assert client.get(path).json()["can_archive"]
    assert client.patch(path, json={"archived": False}).status_code == 200
    login()
    assert client.patch(path, json={"archived": True}).status_code == 200
    login(200)
    with SessionLocal.begin() as db:
        db.get(User, developer.id).role = "admin"
    assert client.patch(path, json={"archived": False}).status_code == 200


def test_unassigned_project_member_cannot_archive_or_restore_other_tasks(client, login):
    login()
    project = client.post("/api/projects", json={"name": "Shared board"}).json()
    client.post(f"/api/projects/{project['id']}/members", json={"email": "test2@hire.me"})
    task = client.post(
        "/api/tasks",
        json={"text": "Another person's task", "status": "completed", "project_id": project["id"]},
    ).json()
    path = f"/api/tasks/{task['id']}"
    login(200)
    assert not client.get(path).json()["can_archive"]
    assert client.patch(path, json={"archived": True}).status_code == 403
    login()
    assert client.patch(path, json={"archived": True}).status_code == 200
    login(200)
    assert not client.get(path).json()["can_archive"]
    assert client.patch(path, json={"archived": False}).status_code == 403
