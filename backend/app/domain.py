import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import (
    LoginSession,
    LoginToken,
    Outbox,
    Project,
    ProjectMember,
    Task,
    TelegramSession,
    User,
    VoiceJob,
    now,
)

TASK_PAGE_SIZE = 5


@dataclass(frozen=True)
class TelegramTask:
    id: uuid.UUID
    user_id: uuid.UUID
    text: str
    title: str
    status: str
    source: str
    project_name: str | None
    assignee: dict | None
    due_at: datetime | None


def telegram_tasks(telegram_id: int, page: int = 0) -> tuple[list[TelegramTask], bool]:
    if not 0 <= page <= 10000:
        raise ValueError("Invalid task page")
    with SessionLocal() as db:
        from app.workspace import active_project_tasks, assigned_to, task_scope

        owner = get_user(db, telegram_id, "")
        tasks = list(
            db.execute(
                select(Task, Project.name, User.name)
                .outerjoin(Project, Project.id == Task.project_id)
                .outerjoin(User, User.id == Task.assignee_id)
                .where(
                    task_scope(owner),
                    active_project_tasks(),
                    Task.deleted_at.is_(None),
                    Task.archived_at.is_(None),
                    or_(
                        assigned_to(owner.id),
                        and_(
                            Task.project_id.is_(None),
                            Task.assignee_id.is_(None),
                            Task.user_id == owner.id,
                        ),
                    ),
                )
                .order_by(Task.created_at.desc(), Task.id.desc())
                .offset(page * TASK_PAGE_SIZE)
                .limit(TASK_PAGE_SIZE + 1)
            )
        )
        items = [
            TelegramTask(
                id=task.id,
                user_id=task.user_id,
                text=task.text,
                title=task.title,
                status=task.status,
                source=task.source,
                project_name=project_name,
                assignee={"name": assignee_name} if assignee_name else None,
                due_at=task.due_at,
            )
            for task, project_name, assignee_name in tasks[:TASK_PAGE_SIZE]
        ]
        return items, len(tasks) > TASK_PAGE_SIZE


def logout_telegram_user(telegram_id: int, all_devices: bool = True) -> int:
    """Revoke this Telegram owner's session, and optionally website sessions and sign-in links."""
    with SessionLocal.begin() as db:
        # Match sign-in's owner lock so exchanging an old link cannot race revocation.
        binding = db.get(TelegramSession, telegram_id)
        if binding is None:
            return 0
        owner = binding.user_id
        db.execute(select(User.id).where(User.id == owner).with_for_update())
        from app.chat_cleanup import schedule_cleanup

        schedule_cleanup(db, telegram_id)
        sessions_count = 0
        if all_devices:
            sessions = db.execute(delete(LoginSession).where(LoginSession.user_id == owner))
            db.execute(delete(LoginToken).where(LoginToken.user_id == owner))
            sessions_count = sessions.rowcount
        db.delete(binding)
        return sessions_count


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def outbox(db: Session, kind: str, key: str, **payload: object) -> None:
    db.execute(
        insert(Outbox)
        .values(kind=kind, dedupe_key=key, payload=payload)
        .on_conflict_do_nothing(index_elements=[Outbox.dedupe_key])
    )


def task_event(db: Session, task: Task) -> None:
    recipients = {task.user_id}
    if task.project_id:
        recipients.update(
            db.scalars(
                select(ProjectMember.user_id).where(ProjectMember.project_id == task.project_id)
            )
        )
        recipients.add(db.get(Project, task.project_id).owner_id)
        recipients.update(db.scalars(select(User.id).where(User.role == "admin")))
    for identifier in recipients:
        outbox(
            db,
            "event",
            f"event:{task.id}:{identifier}:{uuid.uuid4()}",
            user_id=str(identifier),
            task_id=str(task.id),
        )


def get_user(db: Session, telegram_id: int, name: str) -> User:
    user = db.scalar(
        select(User)
        .join(TelegramSession)
        .where(
            TelegramSession.telegram_id == telegram_id,
            TelegramSession.expires_at > now(),
            User.password_hash.is_not(None),
        )
    )
    if user is None:
        raise PermissionError("Log in with your email and password first")
    return user


def create_text_task(
    telegram_id: int, name: str, chat_id: int, message_id: int, text: str
) -> uuid.UUID:
    if not text.strip():
        raise ValueError("Task text cannot be empty")
    with SessionLocal.begin() as db:
        user = get_user(db, telegram_id, name)
        task = db.execute(
            insert(Task)
            .values(
                user_id=user.id,
                assignee_id=user.id,
                text=text,
                source="text",
                chat_id=chat_id,
                message_id=message_id,
            )
            .on_conflict_do_nothing(constraint="uq_task_message")
            .returning(Task)
        ).scalar_one_or_none()
        if task:
            task_event(db, task)
            outbox(
                db,
                "notification",
                f"text-confirm:{task.id}",
                chat_id=chat_id,
                recipient_id=str(user.id),
                created_at=now().isoformat(),
                text="Task created. Open /dashboard to manage it.",
            )
            return task.id
        return db.scalar(
            select(Task.id).where(Task.chat_id == chat_id, Task.message_id == message_id)
        )


def queue_voice(
    telegram_id: int,
    name: str,
    chat_id: int,
    message_id: int,
    file_id: str,
    project_id=None,
    assignee_id=None,
    due_at=None,
    *,
    comment_task_id: uuid.UUID | None = None,
    title: str | None = None,
) -> uuid.UUID:
    with SessionLocal.begin() as db:
        user = get_user(db, telegram_id, name)
        from app.workspace import (
            WorkspaceError,
            project_for,
            task_for,
            validate_assignment,
            validate_due,
        )

        if title is not None:
            if not title.strip() or len(title.strip()) > 120:
                raise WorkspaceError(422, "Task title must contain 1–120 characters")
            title = title.strip()

        if comment_task_id is not None:
            task_for(db, user, comment_task_id)
            if (
                project_id is not None
                or assignee_id is not None
                or due_at is not None
                or title is not None
            ):
                raise ValueError("Voice comments cannot create project tasks")
        elif project_id is not None:
            project_for(db, user, project_id, manage=True)
        else:
            assignee_id = assignee_id or user.id
        validate_assignment(db, user, project_id, assignee_id)
        validate_due(due_at)
        return _insert_voice_job(
            db,
            user_id=user.id,
            kind="comment" if comment_task_id is not None else "task",
            comment_task_id=comment_task_id,
            task_title=title,
            chat_id=chat_id,
            message_id=message_id,
            file_id=file_id,
            project_id=project_id,
            assignee_id=assignee_id,
            due_at=due_at,
        )


def _insert_voice_job(db: Session, **values) -> uuid.UUID:
    job = db.execute(
        insert(VoiceJob)
        .values(**values)
        .on_conflict_do_nothing(constraint="uq_voice_message")
        .returning(VoiceJob)
    ).scalar_one_or_none()
    if job:
        enqueue_voice(db, job)
        return job.id
    return db.scalar(
        select(VoiceJob.id).where(
            VoiceJob.chat_id == values["chat_id"], VoiceJob.message_id == values["message_id"]
        )
    )


def queue_voice_title(
    telegram_id: int, name: str, chat_id: int, message_id: int, file_id: str
) -> uuid.UUID:
    """Durably queue a wizard title recording; the worker advances the wizard when done."""
    with SessionLocal.begin() as db:
        user = get_user(db, telegram_id, name)
        return _insert_voice_job(
            db,
            user_id=user.id,
            kind="title",
            chat_id=chat_id,
            message_id=message_id,
            file_id=file_id,
        )


def queue_voice_input(
    telegram_id: int,
    name: str,
    chat_id: int,
    message_id: int,
    file_id: str,
    token: str,
) -> uuid.UUID:
    """Transcribe a spoken form answer without creating a task or applying it yet."""
    if len(token) != 32 or any(character not in "0123456789abcdef" for character in token):
        raise ValueError("Invalid voice input token")
    with SessionLocal.begin() as db:
        user = get_user(db, telegram_id, name)
        return _insert_voice_job(
            db,
            user_id=user.id,
            kind="input",
            input_token=token,
            chat_id=chat_id,
            message_id=message_id,
            file_id=file_id,
        )


def check_task_editable(db: Session, user: User, task_id: uuid.UUID) -> None:
    """Fail fast with the same rules as workspace.update_task (rechecked when applied)."""
    from app.workspace import WorkspaceError, can_manage, ensure_project_active, task_for

    task = task_for(db, user, task_id)
    ensure_project_active(db, task)
    if task.archived_at is not None:
        raise WorkspaceError(409, "Restore this task before editing it")
    if not can_manage(db, user, task):
        raise WorkspaceError(403, "Only the project manager can edit this task")


def queue_voice_edit(
    telegram_id: int,
    name: str,
    chat_id: int,
    message_id: int,
    file_id: str,
    task_id: uuid.UUID,
    field: str,
) -> uuid.UUID:
    """Durably queue a recording that replaces a task's title or description."""
    if field not in {"title", "text"}:
        raise ValueError("Voice edits change only a task title or description")
    with SessionLocal.begin() as db:
        user = get_user(db, telegram_id, name)
        check_task_editable(db, user, task_id)
        return _insert_voice_job(
            db,
            user_id=user.id,
            kind=f"edit_{field}",
            target_task_id=task_id,
            chat_id=chat_id,
            message_id=message_id,
            file_id=file_id,
        )


def enqueue_voice(db: Session, job: VoiceJob) -> None:
    outbox(
        db,
        "transcribe",
        f"voice:{job.id}:{job.generation}",
        job_id=str(job.id),
        generation=job.generation,
    )


def retry_voice(job_id: uuid.UUID, telegram_id: int) -> bool:
    with SessionLocal.begin() as db:
        owner = get_user(db, telegram_id, "")
        job = db.scalar(
            select(VoiceJob)
            .join(User, User.id == VoiceJob.user_id)
            .where(
                VoiceJob.id == job_id,
                VoiceJob.user_id == owner.id,
            )
            .with_for_update(of=VoiceJob)
        )
        if not job or job.status != "failed":
            return False
        if job.comment_task_id:
            from app.workspace import task_for

            task_for(db, owner, job.comment_task_id)
        if job.target_task_id:
            check_task_editable(db, owner, job.target_task_id)
        job.status, job.error, job.attempts = "queued", None, 0
        job.generation += 1
        enqueue_voice(db, job)
        return True


def dashboard_token(telegram_id: int, name: str) -> str:
    token = secrets.token_urlsafe(32)
    with SessionLocal.begin() as db:
        user = get_user(db, telegram_id, name)
        db.execute(select(User.id).where(User.id == user.id).with_for_update())
        # Recheck the binding after any concurrent sign-out-all has completed.
        user = get_user(db, telegram_id, name)
        db.add(
            LoginToken(
                token_hash=digest(token), user_id=user.id, expires_at=now() + timedelta(minutes=5)
            )
        )
    return f"{settings().app_origin}/dashboard#token={token}"
