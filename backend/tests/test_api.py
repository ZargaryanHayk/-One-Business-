import secrets
import uuid
from datetime import timedelta

import pytest
import redis
from sqlalchemy import func, select

from app.db import SessionLocal
from app.domain import create_text_task, dashboard_token, digest
from app.models import LoginSession, LoginToken, Outbox, Task, now


@pytest.mark.parametrize(
    "username,expected",
    [
        ("taskline_test_bot", "https://t.me/taskline_test_bot"),
        ("https://evil.example", None),
        (None, None),
    ],
)
def test_public_bot_link_has_no_credentials(client, username, expected):
    from app.config import bot_username_key, settings

    cache = redis.Redis.from_url(settings().redis_url)
    if username:
        cache.set(bot_username_key(), username)
    result = client.get("/api/config")
    assert result.status_code == 200
    assert result.json() == {"telegram_bot_url": expected}
    cache.close()


def test_authentication_and_one_time_link(client):
    assert client.get("/api/tasks").status_code == 401
    link = dashboard_token(100, "Alex")
    assert "/dashboard#token=" in link
    token = link.split("#token=")[1]
    with SessionLocal() as db:
        assert db.get(LoginToken, digest(token))
        assert not db.get(LoginToken, token)
    result = client.post("/api/auth/exchange", json={"token": token})
    assert result.status_code == 200
    assert "HttpOnly" in result.headers["set-cookie"]
    assert "SameSite=lax" in result.headers["set-cookie"]
    assert client.get("/api/me").json()["name"] == "Alex"
    assert client.post("/api/auth/exchange", json={"token": token}).status_code == 401


def test_expired_link_session_and_logout(client, login):
    token = dashboard_token(100, "Alex").split("#token=")[1]
    with SessionLocal.begin() as db:
        db.get(LoginToken, digest(token)).expires_at = now() - timedelta(seconds=1)
    assert client.post("/api/auth/exchange", json={"token": token}).status_code == 401
    login()
    with SessionLocal.begin() as db:
        for session in db.scalars(select(LoginSession)):
            session.expires_at = now() - timedelta(seconds=1)
    assert client.get("/api/me").status_code == 401
    login()
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/tasks").status_code == 401


def test_origin_validation_cannot_consume_link(client):
    token = dashboard_token(100, "Alex").split("#token=")[1]
    assert (
        client.post(
            "/api/auth/exchange", json={"token": token}, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert client.post("/api/auth/exchange", json={"token": token}).status_code == 200
    assert client.post("/api/auth/logout", headers={"Origin": "null"}).status_code == 403
    assert client.get("/api/me").status_code == 200


def test_text_task_is_idempotent_private_and_preserves_content(client, login):
    login()
    task_id = create_text_task(100, "Alex", 100, 7, "  Plan release\nKeep the full text.  ")
    assert create_text_task(100, "Alex", 100, 7, "duplicate") == task_id
    other = create_text_task(200, "Jamie", 200, 7, "Private task")
    tasks = client.get("/api/tasks").json()
    assert len(tasks) == 1
    assert tasks[0]["text"] == "  Plan release\nKeep the full text.  "
    assert tasks[0]["source"] == "text"
    assert tasks[0]["status"] == "pending"
    assert client.patch(f"/api/tasks/{other}", json={"status": "completed"}).status_code == 404
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 2
        assert db.scalar(select(func.count()).select_from(Outbox)) == 4


def test_all_status_transitions_and_validation(client, login):
    login()
    task_id = create_text_task(100, "Alex", 100, 1, "Review the proposal")
    for status in ["in_progress", "completed", "pending", "completed", "in_progress"]:
        response = client.patch(f"/api/tasks/{task_id}", json={"status": status})
        assert response.status_code == 200
        assert response.json()["status"] == status
    assert client.patch(f"/api/tasks/{task_id}", json={"status": "invalid"}).status_code == 422
    assert (
        client.patch(
            f"/api/tasks/{task_id}",
            json={"status": "pending"},
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    assert client.patch(f"/api/tasks/{uuid.uuid4()}", json={"status": "pending"}).status_code == 404
    assert client.patch("/api/tasks/invalid", json={"status": "pending"}).json() == {
        "error": {"message": "Invalid request"},
    }


def test_invalid_token_and_security_headers(client):
    assert client.post("/api/auth/exchange", json={"token": "x"}).status_code == 422
    response = client.post("/api/auth/exchange", json={"token": secrets.token_urlsafe(32)})
    assert response.status_code == 401
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").status_code == 200
