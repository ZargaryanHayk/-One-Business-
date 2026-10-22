from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.auth import authenticate, hasher, sign_in_telegram, telegram_user
from app.db import SessionLocal
from app.domain import create_text_task, dashboard_token
from app.models import LoginSession, LoginToken, Task, TelegramSession, User, now


@pytest.mark.parametrize(
    "email,password",
    [
        ("test1@hire.me", "hireme1"),
        ("test2@hire.me", "hireme2"),
    ],
)
def test_email_password_login_and_logout(client, email, password):
    result = client.post("/api/auth/login", json={"email": email, "password": password})
    assert result.status_code == 200
    assert result.json()["email"] == email
    assert "HttpOnly" in result.headers["set-cookie"]
    assert client.get("/api/me").json()["email"] == email
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/tasks").status_code == 401


@pytest.mark.parametrize("all_devices", [False, True])
def test_web_logout_device_scope_and_account_isolation(client, all_devices):
    def login(email, password):
        client.cookies.clear()  # Each login represents a separate device.
        assert (
            client.post("/api/auth/login", json={"email": email, "password": password}).status_code
            == 200
        )
        return client.cookies.get("taskbot_session")

    def use_cookie(token):
        client.cookies.clear()
        client.cookies.set("taskbot_session", token)

    first = login("test1@hire.me", "hireme1")
    second = login("test1@hire.me", "hireme1")
    other = login("test2@hire.me", "hireme2")
    owner = telegram_user(100)
    sign_in_telegram(300, owner)
    link = dashboard_token(100, "Alex").split("#token=")[1]
    other_link = dashboard_token(200, "Jamie").split("#token=")[1]
    task_id = create_text_task(100, "Alex", 100, 1, "Retain task after logout")
    use_cookie(first)
    endpoint = "/api/auth/logout-all" if all_devices else "/api/auth/logout"
    assert client.post(endpoint, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/me").status_code == 200
    assert client.post(endpoint).status_code == 200
    assert client.get("/api/me").status_code == 401
    use_cookie(first)
    assert client.get("/api/me").status_code == 401
    use_cookie(second)
    assert client.get("/api/me").status_code == (401 if all_devices else 200)
    use_cookie(other)
    assert client.get("/api/me").json()["email"] == "test2@hire.me"
    with SessionLocal() as db:
        assert db.get(Task, task_id) is not None
        assert db.get(TelegramSession, 200) is not None
        for identifier in (100, 300):
            assert (db.get(TelegramSession, identifier) is None) == all_devices
        assert db.scalar(
            select(func.count()).select_from(LoginSession).where(LoginSession.user_id == owner.id)
        ) == (0 if all_devices else 1)
        assert db.scalar(
            select(func.count()).select_from(LoginToken).where(LoginToken.user_id == owner.id)
        ) == (0 if all_devices else 1)
    assert client.post("/api/auth/exchange", json={"token": link}).status_code == (
        401 if all_devices else 200
    )
    assert client.post("/api/auth/exchange", json={"token": other_link}).status_code == 200
    client.cookies.clear()
    assert client.post(endpoint).status_code == 401


def test_passwords_are_hashed_and_email_is_normalized(client):
    assert (
        client.post(
            "/api/auth/login",
            json={
                "email": "  TEST1@HIRE.ME  ",
                "password": "hireme1",
            },
        ).status_code
        == 200
    )
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "test1@hire.me"))
        assert user.password_hash.startswith("$argon2id$")
        assert user.password_hash != "hireme1"
        assert hasher.verify(user.password_hash, "hireme1")


@pytest.mark.parametrize(
    "email,password",
    [
        ("test1@hire.me", "incorrect"),
        ("nobody@example.com", "hireme1"),
        ("test1@hire.me", "hireme2"),
        ("test2@hire.me", "hireme1"),
    ],
)
def test_invalid_credentials_do_not_authenticate_or_register(client, email, password):
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 401
    assert response.json() == {"error": {"message": "Email or password is incorrect"}}
    assert client.get("/api/me").status_code == 401
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 2


def test_password_login_rate_limit_and_origin(client):
    body = {"email": "test1@hire.me", "password": "wrong"}
    assert (
        client.post(
            "/api/auth/login", json=body, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    for _ in range(10):
        assert client.post("/api/auth/login", json=body).status_code == 401
    assert client.post("/api/auth/login", json=body).status_code == 429
    assert client.get("/api/tasks").status_code == 401


def test_both_accounts_have_separate_tasks_across_channels(client):
    first = create_text_task(100, "Alex", 100, 1, "Account one task")
    second = create_text_task(200, "Jamie", 200, 1, "Account two task")
    client.post("/api/auth/login", json={"email": "test1@hire.me", "password": "hireme1"})
    assert [task["id"] for task in client.get("/api/tasks").json()] == [str(first)]
    assert client.patch(f"/api/tasks/{second}", json={"status": "completed"}).status_code == 404
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"email": "test2@hire.me", "password": "hireme2"})
    assert [task["id"] for task in client.get("/api/tasks").json()] == [str(second)]
    assert client.patch(f"/api/tasks/{first}", json={"status": "completed"}).status_code == 404


def test_unauthenticated_and_expired_telegram_cannot_create_or_open_dashboard():
    for identifier in (500, 100):
        if identifier == 100:
            with SessionLocal.begin() as db:
                db.get(TelegramSession, 100).expires_at = now() - timedelta(seconds=1)
        assert telegram_user(identifier) is None
        with pytest.raises(PermissionError):
            create_text_task(identifier, "Unknown", identifier, 1, "Blocked task")
        with pytest.raises(PermissionError):
            dashboard_token(identifier, "Unknown")


def test_account_switch_keeps_existing_tasks_with_original_account(client):
    first = create_text_task(100, "Alex", 100, 1, "Account one task")
    account_two = authenticate("test2@hire.me", "hireme2", "test")
    sign_in_telegram(100, account_two)
    second = create_text_task(100, "Alex", 100, 2, "Account two task")
    with SessionLocal() as db:
        assert db.get(Task, first).user_id != account_two.id
        assert db.get(Task, second).user_id == account_two.id


def test_legacy_tasks_follow_their_telegram_owner_on_first_password_login():
    with SessionLocal.begin() as db:
        legacy = User(telegram_id=500, name="Legacy owner")
        db.add(legacy)
        db.flush()
        task = Task(
            user_id=legacy.id, chat_id=500, message_id=1, source="text", text="Keep my old task"
        )
        db.add(task)
        db.flush()
        identifier = task.id
    account = authenticate("test1@hire.me", "hireme1", "test")
    sign_in_telegram(500, account)
    with SessionLocal() as db:
        assert db.get(Task, identifier).user_id == account.id
    second = authenticate("test2@hire.me", "hireme2", "test")
    sign_in_telegram(500, second)
    with SessionLocal() as db:
        assert db.get(Task, identifier).user_id == account.id
