import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.task_titles import default_title


def now() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    email: Mapped[str | None] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(
        String(20), default="developer", server_default="developer", index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        CheckConstraint("role IN ('admin','project_manager','developer')", name="user_role"),
    )


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120))
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ProjectMember(Base):
    __tablename__ = "project_members"
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("chat_id", "message_id", name="uq_task_message"),
        CheckConstraint("status IN ('pending','in_progress','completed')", name="task_status"),
        CheckConstraint(
            "archived_at IS NULL OR status = 'completed'", name="task_archive_completed"
        ),
        CheckConstraint("source IN ('text','voice','dashboard')", name="task_source"),
        CheckConstraint(
            "(source = 'dashboard' AND chat_id IS NULL AND message_id IS NULL) OR "
            "(source IN ('text','voice') AND chat_id IS NOT NULL AND message_id IS NOT NULL)",
            name="task_origin",
        ),
        Index("ix_tasks_owner_created", "user_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"), index=True)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    # Keep the first assignee in the legacy column for existing bot/voice clients.
    additional_assignees: Mapped[list[User]] = relationship(
        secondary="task_assignees", lazy="selectin", order_by="User.name, User.id"
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    text: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(
        String(120), default=default_title, server_default="Untitled task"
    )
    source: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    chat_id: Mapped[int | None] = mapped_column(BigInteger)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def assignee_ids(self) -> list[uuid.UUID]:
        return list(
            dict.fromkeys(
                ([self.assignee_id] if self.assignee_id else [])
                + [person.id for person in self.additional_assignees]
            )
        )


class TaskAssignee(Base):
    __tablename__ = "task_assignees"
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True, index=True)


class TaskComment(Base):
    __tablename__ = "task_comments"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id"), index=True)
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    text: Mapped[str] = mapped_column(Text)
    chat_id: Mapped[int | None] = mapped_column(BigInteger)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("chat_id", "message_id", name="uq_comment_message"),)


class TaskReminder(Base):
    __tablename__ = "task_reminders"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id"))
    recipient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Bounded redelivery for reminders whose send exhausted its retries.
    attempts: Mapped[int] = mapped_column(default=1, server_default="1")
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, server_default=func.now()
    )
    __table_args__ = (
        UniqueConstraint("task_id", "recipient_id", "due_at", name="uq_task_reminder"),
        Index(
            "ix_task_reminders_unsent", "queued_at", postgresql_where=sql_text("sent_at IS NULL")
        ),
    )


class VoiceJob(Base):
    __tablename__ = "voice_jobs"
    __table_args__ = (
        UniqueConstraint("chat_id", "message_id", name="uq_voice_message"),
        CheckConstraint(
            "status IN ('queued','processing','completed','failed')", name="job_status"
        ),
        CheckConstraint(
            "kind IN ('task','comment','title','edit_title','edit_text','input')",
            name="voice_job_kind",
        ),
        CheckConstraint(
            "(kind = 'comment') = (comment_task_id IS NOT NULL) AND "
            "(kind IN ('edit_title','edit_text')) = (target_task_id IS NOT NULL)",
            name="voice_job_target",
        ),
        CheckConstraint("(kind = 'input') = (input_token IS NOT NULL)", name="voice_job_input"),
        Index("ix_voice_jobs_status_updated", "status", "updated_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    # task: create a task; comment: add a comment; title: the Telegram wizard's draft title;
    # edit_title/edit_text: replace a task field; input: confirm a spoken form answer.
    kind: Mapped[str] = mapped_column(String(20), default="task", server_default="task")
    input_token: Mapped[str | None] = mapped_column(String(32))
    comment_task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"))
    target_task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"))
    task_title: Mapped[str | None] = mapped_column(String(120))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"))
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)
    file_id: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    generation: Mapped[int] = mapped_column(default=0)
    attempts: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class LoginToken(Base):
    __tablename__ = "login_tokens"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class LoginSession(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class TelegramLoginToken(Base):
    __tablename__ = "telegram_login_tokens"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_hash: Mapped[str] = mapped_column(
        ForeignKey("sessions.token_hash", ondelete="CASCADE"), index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class TelegramSession(Base):
    __tablename__ = "telegram_sessions"
    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Outbox(Base):
    __tablename__ = "outbox"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSONB)
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Failed publish attempts; the error type only, never payload content.
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(100))
    __table_args__ = (Index("ix_outbox_pending", "delivered_at", "created_at"),)


class TelegramMessage(Base):
    __tablename__ = "telegram_messages"
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ChatCleanup(Base):
    __tablename__ = "chat_cleanups"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    message_ids: Mapped[list[int]] = mapped_column(JSONB)
    reset_done: Mapped[bool] = mapped_column(default=False)
    single_delete: Mapped[bool] = mapped_column(default=False)
    scan_legacy: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
