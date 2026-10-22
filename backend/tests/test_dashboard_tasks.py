import uuid
from unittest.mock import Mock

import pytest
import redis
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.dispatcher import dispatch_once
from app.domain import create_text_task, queue_voice, telegram_tasks
from app.jobs import transcribe
from app.models import Outbox, Task
from app.task_titles import derive_title


def test_title_and_description_are_independent_and_manager_editable(client, login):
    login()
    description = "  Review the release checklist.\nKeep all these details unchanged.  "
    response = client.post("/api/tasks", json={"title": "  Release review  ", "text": description})
    assert response.status_code == 201
    task = response.json()
    assert task["title"] == "Release review" and task["text"] == description
    updated = client.patch(f"/api/tasks/{task['id']}", json={"title": "Ready to ship"})
    assert updated.status_code == 200
    assert updated.json()["text"] == description
    assert updated.json()["title"] == "Ready to ship"
    changed = client.patch(f"/api/tasks/{task['id']}", json={"text": "New description"})
    assert changed.json()["title"] == "Ready to ship"
    assert telegram_tasks(100)[0][0].title == "Ready to ship"
    login(200)
    assert (
        client.patch(f"/api/tasks/{task['id']}", json={"title": "Unauthorized"}).status_code == 404
    )


@pytest.mark.parametrize("title", ["", " \n\t ", "x" * 121])
def test_title_validation_on_creation_and_update(client, login, title):
    login()
    assert client.post("/api/tasks", json={"title": title, "text": "Details"}).status_code == 422
    task = client.post("/api/tasks", json={"title": "Keep title", "text": "Details"}).json()
    assert client.patch(f"/api/tasks/{task['id']}", json={"title": title}).status_code == 422
    assert client.get(f"/api/tasks/{task['id']}").json()["title"] == "Keep title"


@pytest.mark.parametrize("source", ["text", "voice", "dashboard"])
def test_message_sources_generate_bounded_titles_and_preserve_description(
    client, login, monkeypatch, source
):
    login()
    content = "  Multilingual աշխատանք\n" + "Full details " * 100
    if source == "text":
        identifier = create_text_task(100, "Alex", 100, 33, content)
    elif source == "voice":
        job = queue_voice(100, "Alex", 100, 33, "fake-title-fixture")
        monkeypatch.setattr("app.jobs.download_voice", Mock())
        monkeypatch.setattr("app.jobs.transcribe_audio", Mock(return_value=content))
        transcribe.run(str(job), 0)
        identifier = client.get("/api/tasks").json()[0]["id"]
    else:
        identifier = client.post("/api/tasks", json={"text": content}).json()["id"]
    task = client.get(f"/api/tasks/{identifier}").json()
    assert task["text"] == content
    assert task["title"] == derive_title(content)
    assert len(task["title"]) <= 120 and task["title"].endswith("…")


@pytest.mark.parametrize("status", ["pending", "in_progress", "completed"])
def test_dashboard_creates_owned_tasks_in_any_column(client, login, status):
    owner = login()
    content = "  A web task\nwith complete text.  "
    response = client.post("/api/tasks", json={"text": content, "status": status})
    assert response.status_code == 201
    task = response.json()
    assert task["source"] == "dashboard"
    assert task["status"] == status
    assert task["text"] == content
    uuid.UUID(task["id"])
    with SessionLocal() as db:
        saved = db.get(Task, uuid.UUID(task["id"]))
        assert saved.user_id == owner.id
        assert saved.chat_id is None and saved.message_id is None
        event = db.scalar(select(Outbox).where(Outbox.kind == "event"))
        assert event.payload["user_id"] == str(owner.id)
    assert telegram_tasks(100)[0][0].id == uuid.UUID(task["id"])
    login(200)
    assert client.get("/api/tasks").json() == []
    assert client.delete(f"/api/tasks/{task['id']}").status_code == 404


def test_dashboard_task_default_and_status_change(client, login):
    login()
    task = client.post("/api/tasks", json={"text": "A task"}).json()
    assert task["status"] == "pending"
    assert client.patch(f"/api/tasks/{task['id']}", json={"status": "completed"}).status_code == 200


@pytest.mark.parametrize("text", ["", " \n\t ", "x" * 10001])
def test_dashboard_rejects_invalid_task_text(client, login, text):
    login()
    assert client.post("/api/tasks", json={"text": text}).status_code == 422
    assert client.get("/api/tasks").json() == []


def test_create_delete_require_authentication_and_origin(client, login):
    identifier = create_text_task(100, "Alex", 100, 1, "Keep this task")
    assert client.post("/api/tasks", json={"text": "Unauthorized"}).status_code == 401
    assert client.delete(f"/api/tasks/{identifier}").status_code == 401
    login()
    foreign_origin = {"Origin": "https://evil.example"}
    assert (
        client.post(
            "/api/tasks", json={"text": "Invalid origin"}, headers=foreign_origin
        ).status_code
        == 403
    )
    assert client.delete(f"/api/tasks/{identifier}", headers=foreign_origin).status_code == 403
    assert len(client.get("/api/tasks").json()) == 1


@pytest.mark.parametrize("source", ["text", "voice", "dashboard"])
def test_delete_task_hides_it_from_web_and_telegram_without_recreation(
    client, login, monkeypatch, source
):
    login()
    if source == "text":
        identifier = create_text_task(100, "Alex", 100, 1, "Delete this message")
    elif source == "voice":
        job = queue_voice(100, "Alex", 100, 1, "fake-file")
        monkeypatch.setattr("app.jobs.download_voice", Mock())
        monkeypatch.setattr(
            "app.jobs.transcribe_audio", Mock(return_value="Delete this voice task")
        )
        transcribe.run(str(job), 0)
        identifier = uuid.UUID(client.get("/api/tasks").json()[0]["id"])
    else:
        identifier = uuid.UUID(
            client.post("/api/tasks", json={"text": "Delete this web task"}).json()["id"]
        )
    response = client.delete(f"/api/tasks/{identifier}")
    assert response.status_code == 204 and response.content == b""
    assert client.get("/api/tasks").json() == []
    assert telegram_tasks(100) == ([], False)
    assert client.delete(f"/api/tasks/{identifier}").status_code == 404
    assert client.patch(f"/api/tasks/{identifier}", json={"status": "pending"}).status_code == 404
    with SessionLocal() as db:
        assert db.get(Task, identifier).deleted_at is not None
    if source == "text":
        assert create_text_task(100, "Alex", 100, 1, "Duplicate delivery") == identifier
    elif source == "voice":
        transcribe.run(str(job), 0)
    assert client.get("/api/tasks").json() == []


def test_creation_and_deletion_emit_owner_scoped_events(client, login, monkeypatch):
    owner = login()
    cache = redis.Redis.from_url(settings().redis_url)
    with cache.pubsub() as stream:
        stream.subscribe(f"events:{owner.id}")
        stream.get_message(timeout=1)
        task = client.post("/api/tasks", json={"text": "Live task"}).json()
        # Event publication is dispatched only after the write commits.
        assert dispatch_once(cache) == 1
        message = stream.get_message(timeout=2)
        assert str(task["id"]).encode() in message["data"]
        client.delete(f"/api/tasks/{task['id']}")
        assert dispatch_once(cache) == 1
        message = stream.get_message(timeout=2)
        assert str(task["id"]).encode() in message["data"]
    cache.close()
