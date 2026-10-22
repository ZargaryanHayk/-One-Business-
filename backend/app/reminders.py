"""Schedule once per task/deadline/assignee; recheck access and state at delivery."""

import uuid
from datetime import timedelta
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import and_, exists, func, literal, select, union
from sqlalchemy.dialects.postgresql import insert

from app.chat_cleanup import record_http_message
from app.config import settings
from app.db import SessionLocal
from app.domain import outbox
from app.models import Project, Task, TaskAssignee, TaskReminder, TelegramSession, now
from app.transcription import check_rate_limit
from app.workspace import active_project_tasks

REMINDER_WINDOW = timedelta(days=4)
# Overdue tasks are still reminded, but a scan never revisits deadlines older than this.
OVERDUE_LOOKBACK = timedelta(days=7)
SCHEDULE_BATCH = 500
# A reminder whose delivery exhausted its retries is queued again later, a bounded number of times.
RETRY_AFTER = timedelta(minutes=30)
MAX_ATTEMPTS = 4


def contact(db, recipient_id, lock=False):
    query = (
        select(TelegramSession)
        .where(TelegramSession.user_id == recipient_id, TelegramSession.expires_at > now())
        .order_by(TelegramSession.expires_at.desc())
        .limit(1)
    )
    return db.scalar(query.with_for_update() if lock else query)


def eligible_tasks(current):
    return and_(
        Task.due_at.is_not(None),
        Task.due_at <= current + REMINDER_WINDOW,
        Task.due_at >= current - OVERDUE_LOOKBACK,
        Task.status != "completed",
        Task.deleted_at.is_(None),
        active_project_tasks(),
    )


def reminder_recipients(current):
    """Each eligible task's assignees, or its owner for an unassigned personal task."""
    eligible = eligible_tasks(current)
    primary = select(
        Task.id.label("task_id"), Task.assignee_id.label("recipient_id"), Task.due_at
    ).where(eligible, Task.assignee_id.is_not(None))
    additional = (
        select(TaskAssignee.task_id, TaskAssignee.user_id, Task.due_at)
        .join(Task, Task.id == TaskAssignee.task_id)
        .where(eligible)
    )
    owner = select(Task.id, Task.user_id, Task.due_at).where(
        eligible,
        Task.project_id.is_(None),
        Task.assignee_id.is_(None),
        ~exists().where(TaskAssignee.task_id == Task.id),
    )
    return union(primary, additional, owner).subquery()


def schedule_reminders() -> int:
    """One reminder per task, recipient, and deadline, created in a single bounded statement."""
    current = now()
    candidates = reminder_recipients(current)
    fresh = (
        select(
            func.gen_random_uuid(),
            candidates.c.task_id,
            candidates.c.recipient_id,
            candidates.c.due_at,
            literal(current),
            literal(current),
            literal(1),
        )
        .where(
            exists().where(
                TelegramSession.user_id == candidates.c.recipient_id,
                TelegramSession.expires_at > current,
            ),
            ~exists().where(
                TaskReminder.task_id == candidates.c.task_id,
                TaskReminder.recipient_id == candidates.c.recipient_id,
                TaskReminder.due_at == candidates.c.due_at,
            ),
        )
        .limit(SCHEDULE_BATCH)
    )
    with SessionLocal.begin() as db:
        identifiers = list(
            db.scalars(
                insert(TaskReminder)
                .from_select(
                    [
                        "id",
                        "task_id",
                        "recipient_id",
                        "due_at",
                        "created_at",
                        "queued_at",
                        "attempts",
                    ],
                    fresh,
                )
                .on_conflict_do_nothing(constraint="uq_task_reminder")
                .returning(TaskReminder.id)
            )
        )
        for identifier in identifiers:
            outbox(db, "reminder", f"reminder:{identifier}", reminder_id=str(identifier))
        retried = 0
        for reminder in db.scalars(
            select(TaskReminder)
            .join(Task, Task.id == TaskReminder.task_id)
            .where(
                TaskReminder.sent_at.is_(None),
                TaskReminder.queued_at < current - RETRY_AFTER,
                TaskReminder.attempts < MAX_ATTEMPTS,
                TaskReminder.due_at == Task.due_at,
                eligible_tasks(current),
            )
            .limit(100)
            .with_for_update(of=TaskReminder, skip_locked=True)
        ):
            reminder.attempts += 1
            reminder.queued_at = current
            outbox(
                db,
                "reminder",
                f"reminder:{reminder.id}:{reminder.attempts}",
                reminder_id=str(reminder.id),
            )
            retried += 1
    return len(identifiers) + retried


def deliver(task, binding, heading, db):
    due = (
        task.due_at.astimezone(ZoneInfo(settings().app_timezone)).strftime("%Y-%m-%d %H:%M")
        if task.due_at
        else "Not set"
    )
    text = (
        f"{heading}\n\n📝 {task.title}\n📄 {task.text[:1500]}"
        f"\n\nDeadline: {due} ({settings().app_timezone})"
    )
    response = httpx.post(
        f"https://api.telegram.org/bot{settings().telegram_bot_token}/sendMessage",
        json={
            "chat_id": binding.telegram_id,
            "text": text,
            "reply_markup": {
                "inline_keyboard": [[{"text": "Open task", "callback_data": f"task:{task.id}"}]]
            },
        },
        timeout=20,
    )
    check_rate_limit(response)
    if response.status_code in {400, 403, 404}:
        return False
    response.raise_for_status()
    record_http_message(response, db)
    return True


def project_archived(db, task):
    return task.project_id is not None and db.get(Project, task.project_id).archived_at is not None


def send_reminder(identifier: str):
    with SessionLocal.begin() as db:
        reminder = db.scalar(
            select(TaskReminder).where(TaskReminder.id == uuid.UUID(identifier)).with_for_update()
        )
        if reminder is None or reminder.sent_at is not None:
            return
        # The reminder row lock prevents duplicates; the task itself need not stay locked
        # while Telegram is called.
        task = db.get(Task, reminder.task_id)
        if (
            task is None
            or task.deleted_at
            or project_archived(db, task)
            or task.status == "completed"
            or task.due_at != reminder.due_at
        ):
            return
        recipient_id = reminder.recipient_id
        recipients = task.assignee_ids or ([task.user_id] if task.project_id is None else [])
        if recipient_id not in recipients or task.due_at > now() + REMINDER_WINDOW:
            return
        binding = contact(db, recipient_id, lock=True)
        if binding and deliver(
            task, binding, "Deadline reminder: this task is due soon or overdue", db
        ):
            reminder.sent_at = now()


def send_assignment(task_id: str, recipient_id: str):
    with SessionLocal.begin() as db:
        task = db.get(Task, uuid.UUID(task_id))
        if (
            task is None
            or task.deleted_at
            or project_archived(db, task)
            or task.archived_at
            or uuid.UUID(recipient_id) not in task.assignee_ids
        ):
            return
        binding = contact(db, uuid.UUID(recipient_id), lock=True)
        if binding:
            deliver(task, binding, "A project task has been assigned to you", db)
