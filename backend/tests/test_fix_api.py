from urllib.parse import parse_qs, urlparse

import pytest
import redis
from sqlalchemy import func, select

from app.auth import consume_telegram_login, peek_telegram_login, telegram_user
from app.config import bot_username_key, settings
from app.db import SessionLocal
from app.models import LoginSession, Task, User


@pytest.mark.parametrize(
    "due_at",
    [
        "0001-01-01T00:00:00+14:00",  # UTC instant falls before year 1
        "9999-12-31T23:59:00-14:00",  # UTC instant falls after year 9999
        "1969-12-31T23:59:00Z",
        "2101-01-01T00:00:00Z",
    ],
)
def test_out_of_range_deadlines_are_rejected_and_board_stays_readable(client, login, due_at):
    login()
    response = client.post("/api/tasks", json={"text": "Edge deadline", "due_at": due_at})
    assert response.status_code == 422
    assert "between 1970 and 2100" in response.json()["error"]["message"]
    task = client.post("/api/tasks", json={"text": "Valid"}).json()
    assert client.patch(f"/api/tasks/{task['id']}", json={"due_at": due_at}).status_code == 422
    assert client.get("/api/tasks").status_code == 200
    assert client.get(f"/api/tasks/{task['id']}").json()["due_at"] is None


def test_boundary_deadlines_are_accepted(client, login):
    login()
    for due_at in ["1970-01-01T00:00:00Z", "2100-12-31T23:59:00Z"]:
        assert client.post("/api/tasks", json={"text": "Edge", "due_at": due_at}).status_code == 201
    assert len(client.get("/api/tasks").json()) == 2


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/tasks", {"text": "a\x00b"}),
        ("/api/tasks", {"text": "ok", "title": "a\x00b"}),
        ("/api/projects", {"name": "a\x00b"}),
        ("/api/auth/login", {"email": "a\x00@b.c", "password": "x"}),
    ],
)
def test_nul_characters_are_rejected_with_422(client, login, path, body):
    login()
    response = client.post(path, json=body)
    assert response.status_code == 422
    assert response.json() == {"error": {"message": "Invalid request"}}


def test_nul_characters_rejected_in_comments_and_updates(client, login):
    login()
    task = client.post("/api/tasks", json={"text": "Task"}).json()
    assert (
        client.post(f"/api/tasks/{task['id']}/comments", json={"text": "\x00"}).status_code == 422
    )
    assert client.patch(f"/api/tasks/{task['id']}", json={"text": "x\x00"}).status_code == 422


@pytest.mark.parametrize(
    "query", ["limit=-1", "limit=0", "limit=1001", "offset=-5", "limit=99999999999999999999"]
)
def test_list_paging_bounds_return_422(client, login, query):
    login()
    response = client.get(f"/api/tasks?{query}")
    assert response.status_code == 422


def test_list_paging_returns_disjoint_pages(client, login):
    login()
    for index in range(5):
        client.post("/api/tasks", json={"text": f"Task {index}"})
    first = client.get("/api/tasks?limit=3").json()
    second = client.get("/api/tasks?limit=3&offset=3").json()
    assert len(first) == 3 and len(second) == 2
    assert not {task["id"] for task in first} & {task["id"] for task in second}


def session_count(email):
    with SessionLocal() as db:
        return db.scalar(
            select(func.count()).select_from(LoginSession).join(User).where(User.email == email)
        )


def test_logging_in_again_replaces_this_browsers_previous_session(client):
    body = {"email": "test1@hire.me", "password": "hireme1"}
    assert client.post("/api/auth/login", json=body).status_code == 200
    assert client.post("/api/auth/login", json=body).status_code == 200
    assert session_count("test1@hire.me") == 1
    other = {"email": "test2@hire.me", "password": "hireme2"}
    assert client.post("/api/auth/login", json=other).status_code == 200
    assert session_count("test1@hire.me") == 0
    assert client.get("/api/me").json()["email"] == "test2@hire.me"


def issue_link(client):
    assert (
        client.post(
            "/api/auth/login", json={"email": "test1@hire.me", "password": "hireme1"}
        ).status_code
        == 200
    )
    cache = redis.Redis.from_url(settings().redis_url)
    cache.set(bot_username_key(), "taskline_test_bot")
    cache.close()
    url = urlparse(client.post("/api/auth/telegram-link").json()["url"])
    return parse_qs(url.query)["start"][0][4:]


def test_peek_identifies_link_owner_without_consuming(client):
    token = issue_link(client)
    assert peek_telegram_login(token).email == "test1@hire.me"
    assert peek_telegram_login(token).email == "test1@hire.me"
    assert peek_telegram_login("x" * 43) is None
    assert consume_telegram_login(token, 555).email == "test1@hire.me"
    assert peek_telegram_login(token) is None


def test_sign_in_link_never_migrates_legacy_telegram_data(client):
    with SessionLocal.begin() as db:
        legacy = User(telegram_id=777, name="Legacy victim")
        db.add(legacy)
        db.flush()
        db.add(
            Task(
                user_id=legacy.id,
                text="Private legacy note",
                source="text",
                chat_id=777,
                message_id=1,
            )
        )
        legacy_id = legacy.id
    token = issue_link(client)
    assert consume_telegram_login(token, 777).email == "test1@hire.me"
    assert telegram_user(777).email == "test1@hire.me"
    with SessionLocal() as db:
        assert db.scalar(select(Task.user_id).where(Task.text == "Private legacy note")) == (
            legacy_id
        )


def test_moving_project_task_to_personal_transfers_ownership(client, login):
    pm = login()
    with SessionLocal.begin() as db:
        admin = User(email="admin@example.test", name="Admin", role="admin", password_hash="x")
        db.add(admin)
        db.flush()
        admin_id = admin.id
    project = client.post("/api/projects", json={"name": "Moves"}).json()
    task = client.post("/api/tasks", json={"text": "Move me", "project_id": project["id"]}).json()
    with SessionLocal.begin() as db:
        db.get(Task, task["id"]).user_id = admin_id
    moved = client.patch(f"/api/tasks/{task['id']}", json={"project_id": None, "assignee_ids": []})
    assert moved.status_code == 200
    assert moved.json()["user_id"] == str(pm.id)
    assert moved.json()["assignee_ids"] == [str(pm.id)]
    assert task["id"] in {item["id"] for item in client.get("/api/tasks?mine=true").json()}
