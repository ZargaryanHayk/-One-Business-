import uuid
from datetime import timedelta
from unittest.mock import Mock

from sqlalchemy import select

from app.db import SessionLocal
from app.domain import telegram_tasks
from app.models import Outbox, TaskReminder, now
from app.reminders import schedule_reminders, send_assignment, send_reminder
from tests.test_projects import setup_project


def test_multiple_assignees_share_task_permissions_and_lists(client, login):
    pm, developer, project, _ = setup_project(client, login)
    ids = [str(pm.id), str(developer.id)]
    response = client.post(
        "/api/tasks",
        json={
            "text": "Build together",
            "project_id": project["id"],
            "assignee_ids": ids + ids,
        },
    )
    assert response.status_code == 201, response.text
    task = response.json()
    assert task["assignee_ids"] == ids
    assert [person["id"] for person in task["assignees"]] == ids
    assert task["assignee_id"] == ids[0]
    assert task["id"] in {t["id"] for t in client.get("/api/tasks?mine=true").json()}
    login(200)
    assert task["id"] in {t["id"] for t in client.get("/api/tasks?mine=true").json()}
    assert uuid.UUID(task["id"]) in {t.id for t in telegram_tasks(200)[0]}
    assert client.get("/api/projects").json()[0]["id"] == project["id"]
    viewed = client.get(f"/api/tasks/{task['id']}").json()
    assert viewed["can_move"] and viewed["can_archive"] and not viewed["can_manage"]
    assert client.patch(f"/api/tasks/{task['id']}", json={"assignee_ids": []}).status_code == 403
    assert client.patch(f"/api/tasks/{task['id']}", json={"status": "completed"}).status_code == 200
    assert client.patch(f"/api/tasks/{task['id']}", json={"archived": True}).status_code == 200
    assert client.patch(f"/api/tasks/{task['id']}", json={"archived": False}).status_code == 200


def test_edit_clear_and_legacy_assignment_replace_the_full_selection(client, login):
    pm, developer, _, task = setup_project(client, login)
    path = f"/api/tasks/{task['id']}"
    ids = [str(pm.id), str(developer.id)]
    assert client.patch(path, json={"assignee_ids": ids}).json()["assignee_ids"] == ids
    # Old bot/API clients can still replace the selection with one person.
    assert client.patch(path, json={"assignee_id": str(pm.id)}).json()["assignee_ids"] == ids[:1]
    cleared = client.patch(path, json={"assignee_ids": []}).json()
    assert cleared["assignee_ids"] == [] and cleared["assignees"] == []
    assert cleared["assignee"] is None and cleared["assignee_id"] is None
    login(200)
    assert client.get("/api/tasks?mine=true").json() == []
    assert not client.get(path).json()["can_move"]


def test_multiple_assignment_validates_every_member_and_personal_privacy(client, login):
    pm = login()
    project = client.post("/api/projects", json={"name": "Private"}).json()
    invalid = [str(pm.id), str(uuid.uuid4())]
    for data in (
        {"project_id": project["id"], "assignee_ids": invalid},
        {"assignee_ids": invalid},
        {"project_id": project["id"], "assignee_ids": [], "assignee_id": str(pm.id)},
    ):
        assert client.post("/api/tasks", json={"text": "Invalid", **data}).status_code == 422
    assert client.get("/api/tasks").json() == []


def test_member_removal_preserves_other_assignees(client, login):
    pm, developer, project, task = setup_project(client, login)
    path = f"/api/tasks/{task['id']}"
    # Removing the first person promotes a remaining assignee.
    client.patch(path, json={"assignee_ids": [str(developer.id), str(pm.id)]})
    assert client.delete(f"/api/projects/{project['id']}/members/{developer.id}").status_code == 204
    updated = client.get(path).json()
    assert updated["assignee_ids"] == [str(pm.id)]
    assert updated["assignee_id"] == str(pm.id)
    assert [person["id"] for person in updated["assignees"]] == [str(pm.id)]
    login(200)
    assert client.get(path).status_code == 404


def test_notifications_and_reminders_reach_each_assignee_and_skip_removed_people(
    client,
    login,
    monkeypatch,
):
    pm, developer, _, task = setup_project(client, login, now() + timedelta(days=2))
    path = f"/api/tasks/{task['id']}"
    client.patch(path, json={"assignee_ids": [str(pm.id), str(developer.id)]})
    with SessionLocal() as db:
        recipients = [
            entry.payload["recipient_id"]
            for entry in db.scalars(select(Outbox).where(Outbox.kind == "assignment"))
        ]
    assert sorted(recipients) == sorted([str(pm.id), str(developer.id)])
    assert schedule_reminders() == 2
    assert schedule_reminders() == 0
    send = Mock(return_value=Mock(status_code=200))
    monkeypatch.setattr("app.reminders.httpx.post", send)
    for identifier in (pm.id, developer.id):
        send_assignment(task["id"], str(identifier))
    assert {call.kwargs["json"]["chat_id"] for call in send.call_args_list} == {100, 200}
    send.reset_mock()
    client.patch(path, json={"assignee_ids": [str(pm.id)]})
    send_assignment(task["id"], str(developer.id))
    with SessionLocal() as db:
        identifiers = list(db.scalars(select(TaskReminder.id)))
    for identifier in identifiers:
        send_reminder(str(identifier))
    assert send.call_count == 1
    assert send.call_args.kwargs["json"]["chat_id"] == 100
