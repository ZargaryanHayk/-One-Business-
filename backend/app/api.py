import asyncio
import json
import logging
import re
import secrets
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta
from typing import Literal

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from redis.exceptions import RedisError
from sqlalchemy import delete, select, text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import workspace
from app.auth import LoginRateLimited, authenticate
from app.chat_cleanup import schedule_account_cleanup
from app.config import bot_username_key, settings
from app.db import SessionLocal
from app.domain import digest
from app.logging import configure_logging
from app.models import LoginSession, LoginToken, TelegramLoginToken, TelegramSession, User, now

COOKIE = "taskbot_session"


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    app.state.redis = aioredis.from_url(settings().redis_url, decode_responses=True)
    yield
    await app.state.redis.aclose()


app = FastAPI(title="AI Task Bot", version="1.0.0", lifespan=lifespan)


@app.exception_handler(workspace.WorkspaceError)
async def workspace_error(request: Request, error: workspace.WorkspaceError):
    return JSONResponse({"error": {"message": error.message}}, status_code=error.status)


@app.exception_handler(Exception)
async def unexpected_error(request: Request, error: Exception):
    logging.getLogger(__name__).error(
        "api_request_failed", extra={"error_type": type(error).__name__}
    )
    return JSONResponse(
        {"error": {"message": "Service temporarily unavailable"}},
        status_code=500,
        headers={"Cache-Control": "no-store"},
    )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, error: StarletteHTTPException):
    return JSONResponse(
        {"error": {"message": str(error.detail)}},
        status_code=error.status_code,
        headers=getattr(error, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error: RequestValidationError):
    return JSONResponse({"error": {"message": "Invalid request"}}, status_code=422)


def require_origin(request: Request) -> None:
    if request.headers.get("origin") != settings().app_origin:
        raise HTTPException(403, "Request origin is not allowed")


def session_user(request: Request) -> User:
    token = request.cookies.get(COOKIE, "")
    with SessionLocal() as db:
        user = db.scalar(
            select(User)
            .join(LoginSession)
            .where(
                LoginSession.token_hash == digest(token),
                LoginSession.expires_at > now(),
                User.password_hash.is_not(None),
            )
        )
        if not user:
            raise HTTPException(401, "Log in with your email and password")
        return user


class Body(BaseModel):
    @field_validator("*", mode="before")
    @classmethod
    def reject_nul(cls, value):
        # PostgreSQL text columns cannot store NUL characters.
        if isinstance(value, str) and "\x00" in value:
            raise ValueError("Text cannot contain NUL characters")
        return value


class ExchangeBody(Body):
    token: str = Field(min_length=40, max_length=100)


class LoginBody(Body):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class VoiceCorrectionBody(Body):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=10000)
    revision: int = Field(ge=0)


@app.get("/api/voice-review")
def voice_review(request: Request):
    from app.voice_review import editor_view

    return editor_view(request.headers.get("X-Voice-Edit-Token", ""))


def require_editor_origin(request: Request) -> None:
    from app.voice_input import editor_origin

    # Only this draft-token endpoint accepts the local Telegram editor alias.
    if request.headers.get("origin") not in {settings().app_origin, editor_origin()}:
        raise HTTPException(403, "Request origin is not allowed")


@app.post("/api/voice-review", dependencies=[Depends(require_editor_origin)])
def correct_voice_review(body: VoiceCorrectionBody, request: Request):
    from app.voice_review import save_editor

    return save_editor(request.headers.get("X-Voice-Edit-Token", ""), body.text, body.revision)


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE,
        token,
        max_age=settings().session_days * 86400,
        httponly=True,
        secure=settings().cookie_secure,
        samesite="lax",
        path="/",
    )


@app.post("/api/auth/login", dependencies=[Depends(require_origin)])
async def password_login(body: LoginBody, request: Request, response: Response):
    source = f"web:{request.client.host if request.client else 'unknown'}"
    try:
        user = await asyncio.to_thread(authenticate, body.email, body.password, source)
    except LoginRateLimited:
        raise HTTPException(429, "Too many login attempts. Try again in a minute.") from None
    except RedisError:
        raise HTTPException(503, "Login temporarily unavailable. Please try again.") from None
    if user is None:
        raise HTTPException(401, "Email or password is incorrect")
    token = await asyncio.to_thread(
        issue_session, user.id, request.cookies.get(COOKIE, ""), lock_user=True
    )
    set_session_cookie(response, token)
    return workspace.person(user)


def issue_session(user_id: uuid.UUID, previous_token: str, *, lock_user: bool, db=None) -> str:
    """Create a browser session, replacing the one this browser presented (if any)."""
    if db is None:
        with SessionLocal.begin() as db:
            return issue_session(user_id, previous_token, lock_user=lock_user, db=db)
    if lock_user:
        db.execute(select(User.id).where(User.id == user_id).with_for_update())
    if previous_token:
        db.execute(delete(LoginSession).where(LoginSession.token_hash == digest(previous_token)))
    token = secrets.token_urlsafe(32)
    db.add(
        LoginSession(
            token_hash=digest(token),
            user_id=user_id,
            expires_at=now() + timedelta(days=settings().session_days),
        )
    )
    return token


class TaskStatusBody(Body):
    status: Literal["pending", "in_progress", "completed"]


class TaskCreateBody(TaskStatusBody):
    status: Literal["pending", "in_progress", "completed"] = "pending"
    text: str = Field(min_length=1, max_length=10000)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    project_id: uuid.UUID | None = None
    assignee_id: uuid.UUID | None = None
    assignee_ids: list[uuid.UUID] | None = Field(default=None, max_length=200)
    due_at: AwareDatetime | None = None

    @field_validator("text")
    @classmethod
    def nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Task text cannot be empty")
        return value


class TaskView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    text: str
    title: str
    source: Literal["text", "voice", "dashboard"]
    status: Literal["pending", "in_progress", "completed"]
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None
    user_id: uuid.UUID
    project_id: uuid.UUID | None
    project_name: str | None
    project_archived: bool
    assignee_id: uuid.UUID | None
    assignee: dict | None
    assignee_ids: list[uuid.UUID]
    assignees: list[dict]
    due_at: datetime | None
    can_manage: bool
    can_move: bool
    can_archive: bool


class TaskUpdateBody(Body):
    model_config = ConfigDict(extra="forbid")
    archived: bool | None = None
    status: Literal["pending", "in_progress", "completed"] | None = None
    text: str | None = Field(default=None, min_length=1, max_length=10000)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    project_id: uuid.UUID | None = None
    assignee_id: uuid.UUID | None = None
    assignee_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    due_at: AwareDatetime | None = None


class ProjectBody(Body):
    name: str = Field(min_length=1, max_length=120)


class ProjectArchiveBody(Body):
    model_config = ConfigDict(extra="forbid")
    archived: bool


class MemberBody(Body):
    email: str = Field(min_length=3, max_length=254)


class CommentBody(Body):
    text: str = Field(min_length=1, max_length=4000)


@app.post("/api/auth/exchange", dependencies=[Depends(require_origin)])
def exchange(body: ExchangeBody, request: Request, response: Response):
    with SessionLocal.begin() as db:
        token_hash = digest(body.token)
        owner = db.scalar(select(LoginToken.user_id).where(LoginToken.token_hash == token_hash))
        if owner is not None:
            # Serialize session issuance with Telegram's sign-out-all operation.
            db.execute(select(User.id).where(User.id == owner).with_for_update())
        login = db.execute(
            delete(LoginToken)
            .where(
                LoginToken.token_hash == token_hash,
                LoginToken.expires_at > now(),
            )
            .returning(LoginToken)
        ).scalar_one_or_none()
        if not login:
            raise HTTPException(
                401, "Link expired or already used. Request /dashboard in Telegram."
            )
        token = issue_session(
            login.user_id, request.cookies.get(COOKIE, ""), lock_user=False, db=db
        )
    set_session_cookie(response, token)
    return {"ok": True}


@app.get("/api/me")
def me(user: User = Depends(session_user)):
    return workspace.person(user)


@app.get("/api/config")
async def public_config(request: Request):
    try:
        username = await asyncio.wait_for(
            request.app.state.redis.get(bot_username_key()), timeout=2
        )
    except (aioredis.RedisError, TimeoutError):
        username = None
    url = None
    if username and re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
        url = f"https://t.me/{username}"
    return {"telegram_bot_url": url}


@app.post("/api/auth/telegram-link", dependencies=[Depends(require_origin)])
async def telegram_login_link(request: Request, user: User = Depends(session_user)):
    config = await public_config(request)
    if not config["telegram_bot_url"]:
        raise HTTPException(503, "Telegram is temporarily unavailable. Please try again.")
    token = await asyncio.to_thread(
        issue_telegram_link, user.id, digest(request.cookies.get(COOKIE, ""))
    )
    return {"url": f"{config['telegram_bot_url']}?start=web_{token}"}


def issue_telegram_link(user_id: uuid.UUID, session_hash: str) -> str:
    token = secrets.token_urlsafe(32)
    with SessionLocal.begin() as db:
        db.execute(select(User.id).where(User.id == user_id).with_for_update())
        session = db.scalar(
            select(LoginSession).where(
                LoginSession.token_hash == session_hash,
                LoginSession.user_id == user_id,
                LoginSession.expires_at > now(),
            )
        )
        if session is None:
            raise HTTPException(401, "Log in with your email and password")
        # Each click replaces this browser's outstanding link.
        db.execute(
            delete(TelegramLoginToken).where(TelegramLoginToken.session_hash == session_hash)
        )
        db.add(
            TelegramLoginToken(
                token_hash=digest(token),
                session_hash=session_hash,
                expires_at=min(now() + timedelta(minutes=5), session.expires_at),
            )
        )
    return token


@app.post("/api/auth/logout", dependencies=[Depends(require_origin)])
def logout(request: Request, response: Response, user: User = Depends(session_user)):
    with SessionLocal.begin() as db:
        db.execute(select(User.id).where(User.id == user.id).with_for_update())
        schedule_account_cleanup(db, user.id)
        db.execute(
            delete(LoginSession).where(
                LoginSession.token_hash == digest(request.cookies.get(COOKIE, "")),
            )
        )
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.post("/api/auth/logout-all", dependencies=[Depends(require_origin)])
def logout_all(response: Response, user: User = Depends(session_user)):
    with SessionLocal.begin() as db:
        db.execute(select(User.id).where(User.id == user.id).with_for_update())
        schedule_account_cleanup(db, user.id)
        db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
        db.execute(delete(LoginToken).where(LoginToken.user_id == user.id))
        db.execute(delete(TelegramSession).where(TelegramSession.user_id == user.id))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.get("/api/tasks", response_model=list[TaskView])
def tasks(
    project_id: uuid.UUID | None = None,
    mine: bool = False,
    due_soon: bool = False,
    archived: bool = False,
    limit: int = Query(1000, ge=1, le=1000),
    offset: int = Query(0, ge=0, le=10_000_000),
    user: User = Depends(session_user),
):
    return workspace.list_tasks(
        user, project_id, mine, due_soon, limit=limit, offset=offset, archived=archived
    )


@app.post(
    "/api/tasks", response_model=TaskView, status_code=201, dependencies=[Depends(require_origin)]
)
def create_task(body: TaskCreateBody, user: User = Depends(session_user)):
    return workspace.create_task(user, **body.model_dump())


@app.patch("/api/tasks/{task_id}", response_model=TaskView, dependencies=[Depends(require_origin)])
def update_task(task_id: uuid.UUID, body: TaskUpdateBody, user: User = Depends(session_user)):
    return workspace.update_task(user, task_id, body.model_dump(exclude_unset=True))


@app.delete("/api/tasks/{task_id}", status_code=204, dependencies=[Depends(require_origin)])
def delete_task(task_id: uuid.UUID, user: User = Depends(session_user)):
    workspace.delete_task(user, task_id)
    return Response(status_code=204)


@app.get("/api/tasks/{task_id}", response_model=TaskView)
def get_task(task_id: uuid.UUID, user: User = Depends(session_user)):
    return workspace.get_task(user, task_id)


@app.get("/api/projects")
def projects(archived: bool = False, user: User = Depends(session_user)):
    return workspace.list_projects(user, archived=archived)


@app.patch("/api/projects/{project_id}", dependencies=[Depends(require_origin)])
def archive_project(
    project_id: uuid.UUID, body: ProjectArchiveBody, user: User = Depends(session_user)
):
    return workspace.archive_project(user, project_id, body.archived)


@app.post("/api/projects", status_code=201, dependencies=[Depends(require_origin)])
def create_project(body: ProjectBody, user: User = Depends(session_user)):
    return workspace.create_project(user, body.name)


@app.get("/api/projects/{project_id}/members")
def members(project_id: uuid.UUID, user: User = Depends(session_user)):
    return workspace.members(user, project_id)


@app.post("/api/projects/{project_id}/members", dependencies=[Depends(require_origin)])
def add_member(project_id: uuid.UUID, body: MemberBody, user: User = Depends(session_user)):
    return workspace.add_member(user, project_id, body.email)


@app.delete(
    "/api/projects/{project_id}/members/{member_id}",
    status_code=204,
    dependencies=[Depends(require_origin)],
)
def remove_member(project_id: uuid.UUID, member_id: uuid.UUID, user: User = Depends(session_user)):
    workspace.remove_member(user, project_id, member_id)
    return Response(status_code=204)


@app.get("/api/users")
def users(user: User = Depends(session_user)):
    if not workspace.manager(user):
        raise HTTPException(403, "Only project managers can browse accounts")
    with SessionLocal() as db:
        return [
            workspace.person(p)
            for p in db.scalars(
                select(User).where(User.password_hash.is_not(None)).order_by(User.name)
            )
        ]


@app.get("/api/tasks/{task_id}/comments")
def comments(task_id: uuid.UUID, user: User = Depends(session_user)):
    return workspace.comments(user, task_id)


@app.post("/api/tasks/{task_id}/comments", status_code=201, dependencies=[Depends(require_origin)])
def add_comment(task_id: uuid.UUID, body: CommentBody, user: User = Depends(session_user)):
    return workspace.add_comment(user, task_id, body.text)


def session_alive(token_hash: str) -> bool:
    with SessionLocal() as db:
        return (
            db.scalar(
                select(LoginSession.token_hash).where(
                    LoginSession.token_hash == token_hash,
                    LoginSession.expires_at > now(),
                )
            )
            is not None
        )


MAX_STREAMS_PER_USER = 20
open_streams: dict[uuid.UUID, int] = {}


@app.get("/api/events")
async def events(request: Request, user: User = Depends(session_user)):
    token_hash = digest(request.cookies.get(COOKIE, ""))

    async def stream() -> AsyncIterator[str]:
        # Each stream holds a Redis connection; bound them per account (single API
        # process). Counting inside the generator guarantees the finally block runs.
        if open_streams.get(user.id, 0) >= MAX_STREAMS_PER_USER:
            yield "retry: 30000\nevent: busy\ndata: {}\n\n"
            return
        open_streams[user.id] = open_streams.get(user.id, 0) + 1
        pubsub = request.app.state.redis.pubsub()
        try:
            await pubsub.subscribe(f"events:{user.id}")
            yield "event: ready\ndata: {}\n\n"
            last_check = asyncio.get_running_loop().time()
            while not await request.is_disconnected():
                if asyncio.get_running_loop().time() - last_check >= 5:
                    if not await asyncio.to_thread(session_alive, token_hash):
                        yield "event: signed_out\ndata: {}\n\n"
                        return
                    yield ": heartbeat\n\n"
                    last_check = asyncio.get_running_loop().time()
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
                if message:
                    try:
                        payload = json.loads(message["data"])
                    except (TypeError, ValueError):
                        payload = {}
                    yield f"event: task_changed\ndata: {json.dumps(payload)}\n\n"
        except aioredis.RedisError:
            yield "event: reconnect\ndata: {}\n\n"
        finally:
            remaining = open_streams.get(user.id, 1) - 1
            if remaining > 0:
                open_streams[user.id] = remaining
            else:
                open_streams.pop(user.id, None)
            with suppress(aioredis.RedisError):
                await pubsub.aclose()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-store",
        },
    )


@app.get("/health/live")
def live():
    return {"status": "ok"}


@app.get("/health/ready")
async def ready(request: Request):
    try:

        def check_db():
            with SessionLocal() as db:
                db.execute(text("SELECT 1"))

        await asyncio.to_thread(check_db)
        await request.app.state.redis.ping()
    except Exception:
        raise HTTPException(503, "Dependencies unavailable") from None
    return {"status": "ready"}
