import os
from pathlib import Path

import pytest
import redis
from alembic import command
from alembic.config import Config
from sqlalchemy import delete
from sqlalchemy.engine import make_url

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://taskbot:local-development-change-me@localhost:55432/taskbot_test",
)
os.environ.setdefault("REDIS_URL", "redis://localhost:56379/15")
os.environ.setdefault("APP_ORIGIN", "http://testserver")

from datetime import timedelta  # noqa: E402

from app.api import app  # noqa: E402
from app.auth import hasher  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import TelegramSession, User, now  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def database_schema():
    if os.environ.get("ALLOW_TEST_DATABASE_RESET") != "1":
        pytest.exit(
            "Set ALLOW_TEST_DATABASE_RESET=1 with an isolated *_test database and Redis /15"
        )
    if not (make_url(str(engine.url)).database or "").endswith("_test"):
        pytest.exit("Refusing to modify a database without the _test suffix")
    if not os.environ["REDIS_URL"].endswith("/15"):
        pytest.exit("Tests require an isolated Redis database /15")
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    command.upgrade(config, "head")
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_state(database_schema, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings(), "telegram_bot_token", "123456:synthetic_token_for_tests")

    def require_mocked_cleanup(*args, **kwargs):
        raise AssertionError("Cleanup tests must mock Telegram transport")

    monkeypatch.setattr("app.chat_cleanup.telegram_call", require_mocked_cleanup)
    with SessionLocal.begin() as db:
        for table in reversed(Base.metadata.sorted_tables):
            db.execute(delete(table))
        for telegram_id, email, password, name in [
            (100, "test1@hire.me", "hireme1", "Alex"),
            (200, "test2@hire.me", "hireme2", "Jamie"),
        ]:
            user = User(
                telegram_id=telegram_id,
                email=email,
                password_hash=hasher.hash(password),
                name=name,
                role="project_manager" if telegram_id == 100 else "developer",
            )
            db.add(user)
            db.flush()
            db.add(
                TelegramSession(
                    telegram_id=telegram_id, user_id=user.id, expires_at=now() + timedelta(days=7)
                )
            )
    cache = redis.Redis.from_url(os.environ["REDIS_URL"])
    cache.flushdb()
    yield
    cache.close()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        test_client.headers["Origin"] = os.environ["APP_ORIGIN"]
        yield test_client


@pytest.fixture
def login(client):
    from app.domain import dashboard_token

    def sign_in(telegram_id=100, name="Alex Morgan"):
        from sqlalchemy import select

        from app.auth import sign_in_telegram

        with SessionLocal() as db:
            owner = db.scalar(select(User).where(User.telegram_id == telegram_id))
        sign_in_telegram(telegram_id, owner)
        link = dashboard_token(telegram_id, name)
        token = link.split("#token=")[1]
        # Each sign-in models a separate device; a browser's own re-login replaces its session.
        client.cookies.clear()
        response = client.post("/api/auth/exchange", json={"token": token})
        assert response.status_code == 200
        with SessionLocal() as db:
            from sqlalchemy import select

            return db.scalar(select(User).where(User.telegram_id == telegram_id))

    return sign_in
