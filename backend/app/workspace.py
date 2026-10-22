"""Shared authorization and project operations for HTTP and Telegram."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.domain import outbox, task_event
from app.models import Project, ProjectMember, Task, TaskComment, TaskReminder, User, now
from app.task_titles import derive_title


class WorkspaceError(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message
        super().__init__(message)


def person(user: User) -> dict:
    return {"id": user.id, "name": user.name, "email": user.email, "role": user.role}


def manager(user: User) -> bool:
    return user.role in {"admin", "project_manager"}


def project_scope(user: User):
    return or_(
        Project.owner_id == user.id,
        exists().where(ProjectMember.project_id == Project.id, ProjectMember.user_id == user.id),
        user.role == "admin",
    )


def task_scope(user: User):
    return or_(
        and_(Task.project_id.is_(None), Task.user_id == user.id),
        Task.project_id.in_(select(Project.id).where(project_scope(user))),
    )


def project_for(
    db: Session,
    user: User,
    identifier: uuid.UUID,
    manage: bool = False,
    *,
    allow_archived: bool = False,
) -> Project:
    project = db.scalar(select(Project).where(Project.id == identifier, project_scope(user)))
    if project is None:
        raise WorkspaceError(404, "Project not found")
    if manage and not (user.role == "admin" or manager(user) and project.owner_id == user.id):
        raise WorkspaceError(403, "Only the project manager can manage this project")
    if manage and project.archived_at is not None and not allow_archived:
        raise WorkspaceError(409, "Restore this project before making changes")
    return project


def active_project_tasks():
    return or_(
        Task.project_id.is_(None),
        Task.project_id.in_(select(Project.id).where(Project.archived_at.is_(None))),
    )


def ensure_project_active(db: Session, task: Task):
    if task.project_id and db.get(Project, task.project_id).archived_at is not None:
        raise WorkspaceError(409, "Restore this project before making changes")


def can_manage(db: Session, user: User, task: Task) -> bool:
    if task.project_id is None:
        return task.user_id == user.id
    project = db.get(Project, task.project_id)
    return project.archived_at is None and (
        user.role == "admin" or manager(user) and project.owner_id == user.id
    )


def assigned_to(identifier):
    return or_(Task.assignee_id == identifier, Task.additional_assignees.any(User.id == identifier))


def can_move(db: Session, user: User, task: Task) -> bool:
    project_active = task.project_id is None or db.get(Project, task.project_id).archived_at is None
    return (
        project_active
        and task.archived_at is None
        and (can_manage(db, user, task) or user.id in task.assignee_ids)
    )


def task_for(db: Session, user: User, identifier: uuid.UUID, lock: bool = False) -> Task:
    query = select(Task).where(Task.id == identifier, Task.deleted_at.is_(None), task_scope(user))
    if lock:
        query = query.with_for_update()
    task = db.scalar(query)
    if task is None:
        raise WorkspaceError(404, "Task not found")
    return task


def _can_manage_from_project(user: User, project: Project | None) -> bool:
    """Determine manage permission given a pre-loaded project (may be None)."""
    if project is None:
        return False
    return project.archived_at is None and (
        user.role == "admin" or manager(user) and project.owner_id == user.id
    )


def _task_row_to_dict(
    user: User,
    task: Task,
    assignee: User | None,
    project: Project | None,
) -> dict:
    """Build a task dict from pre-joined ORM objects — no extra DB calls."""
    managed = (
        task.user_id == user.id
        if task.project_id is None
        else _can_manage_from_project(user, project)
    )
    project_archived = project is not None and project.archived_at is not None
    movable = (
        not project_archived
        and task.archived_at is None
        and (managed or user.id in task.assignee_ids)
    )
    assignees = ([assignee] if assignee else []) + [
        person for person in task.additional_assignees if person.id != task.assignee_id
    ]
    return {
        "id": task.id,
        "user_id": task.user_id,
        "text": task.text,
        "title": task.title,
        "source": task.source,
        "status": task.status,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "archived_at": task.archived_at,
        "project_id": task.project_id,
        "project_name": project.name if project else None,
        "project_archived": project_archived,
        "assignee_id": task.assignee_id,
        "assignee": person(assignee) if assignee else None,
        "assignee_ids": task.assignee_ids,
        "assignees": [person(assigned) for assigned in assignees],
        "due_at": task.due_at,
        "can_manage": managed,
        "can_move": movable,
        "can_archive": not project_archived and (managed or user.id in task.assignee_ids),
    }


def task_data(db: Session, task: Task, user: User) -> dict:
    """Build a task dict for write operations where related objects may already be cached."""
    assignee = db.get(User, task.assignee_id) if task.assignee_id else None
    project = db.get(Project, task.project_id) if task.project_id else None
    return _task_row_to_dict(user, task, assignee, project)


def list_tasks(
    user: User,
    project_id=None,
    mine=False,
    due_soon=False,
    limit: int = 200,
    offset: int = 0,
    archived: bool = False,
) -> list[dict]:
    from sqlalchemy.orm import aliased

    AssigneeModel = aliased(User, name="assignee_alias")
    with SessionLocal() as db:
        query = (
            select(Task, Project, AssigneeModel)
            .outerjoin(Project, Project.id == Task.project_id)
            .outerjoin(AssigneeModel, AssigneeModel.id == Task.assignee_id)
            .where(
                task_scope(user),
                Task.deleted_at.is_(None),
                Task.archived_at.is_not(None) if archived else Task.archived_at.is_(None),
            )
        )
        if project_id is not None:
            project_for(db, user, project_id)
            query = query.where(Task.project_id == project_id)
        else:
            query = query.where(active_project_tasks())
        if mine:
            query = query.where(
                or_(
                    assigned_to(user.id),
                    and_(
                        Task.project_id.is_(None),
                        Task.assignee_id.is_(None),
                        Task.user_id == user.id,
                    ),
                )
            )
        if due_soon:
            query = query.where(
                Task.due_at <= now() + timedelta(days=4), Task.status != "completed"
            )
        query = query.order_by(Task.created_at.desc(), Task.id.desc()).offset(offset).limit(limit)
        return [
            _task_row_to_dict(user, task, assignee, project)
            for task, project, assignee in db.execute(query)
        ]


def list_projects(user: User, archived: bool = False) -> list[dict]:
    with SessionLocal() as db:
        query = select(Project).where(
            project_scope(user),
            Project.archived_at.is_not(None) if archived else Project.archived_at.is_(None),
        )
        if user.role == "developer":
            query = query.where(
                exists().where(
                    Task.project_id == Project.id,
                    assigned_to(user.id),
                    Task.deleted_at.is_(None),
                    True if archived else Task.archived_at.is_(None),
                )
            )
        return [
            project_data(user, project)
            for project in db.scalars(query.order_by(Project.created_at, Project.id))
        ]


def project_data(user: User, project: Project) -> dict:
    return {
        "id": project.id,
        "name": project.name,
        "owner_id": project.owner_id,
        "archived_at": project.archived_at,
        "can_manage": user.role == "admin" or manager(user) and project.owner_id == user.id,
    }


def archive_project(user: User, identifier: uuid.UUID, archived: bool) -> dict:
    with SessionLocal.begin() as db:
        project = project_for(db, user, identifier, manage=True, allow_archived=True)
        db.refresh(project, with_for_update=True)
        was_archived = project.archived_at is not None
        project.archived_at = (project.archived_at or now()) if archived else None
        db.flush()
        project_event(db, project)
        if was_archived and not archived:
            # A reminder consumed while archived must get another delivery opportunity.
            for reminder_id in db.scalars(
                select(TaskReminder.id)
                .join(Task, Task.id == TaskReminder.task_id)
                .where(
                    Task.project_id == project.id,
                    Task.deleted_at.is_(None),
                    Task.status != "completed",
                    Task.due_at == TaskReminder.due_at,
                    TaskReminder.sent_at.is_(None),
                )
            ):
                outbox(
                    db,
                    "reminder",
                    f"project-reminder:{reminder_id}:{uuid.uuid4()}",
                    reminder_id=str(reminder_id),
                )
        # Admins also see the project and its tasks in their workspace.
        for identifier in db.scalars(select(User.id).where(User.role == "admin")):
            if identifier not in audience(db, project):
                outbox(
                    db,
                    "event",
                    f"project:{project.id}:{identifier}:{uuid.uuid4()}",
                    user_id=str(identifier),
                    task_id=str(project.id),
                )
        return project_data(user, project)


def audience(db: Session, project: Project) -> set:
    return {
        project.owner_id,
        *db.scalars(select(ProjectMember.user_id).where(ProjectMember.project_id == project.id)),
    }


def project_event(db: Session, project: Project):
    for identifier in audience(db, project):
        outbox(
            db,
            "event",
            f"project:{project.id}:{identifier}:{uuid.uuid4()}",
            user_id=str(identifier),
            task_id=str(project.id),
        )


def create_project(user: User, name: str) -> dict:
    if not manager(user):
        raise WorkspaceError(403, "Only project managers can create projects")
    if not name.strip() or len(name.strip()) > 120:
        raise WorkspaceError(422, "Project name must contain 1–120 characters")
    with SessionLocal.begin() as db:
        project = Project(name=name.strip(), owner_id=user.id)
        db.add(project)
        db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id))
        db.flush()
        project_event(db, project)
        return project_data(user, project)


def members(user: User, project_id: uuid.UUID) -> list[dict]:
    with SessionLocal() as db:
        project_for(db, user, project_id)
        return [
            person(p)
            for p in db.scalars(
                select(User)
                .join(ProjectMember, ProjectMember.user_id == User.id)
                .where(ProjectMember.project_id == project_id)
                .order_by(User.name, User.id)
            )
        ]


def add_member(user: User, project_id: uuid.UUID, email: str) -> dict:
    with SessionLocal.begin() as db:
        project = project_for(db, user, project_id, manage=True)
        db.execute(select(Project.id).where(Project.id == project.id).with_for_update())
        member = db.scalar(
            select(User).where(
                User.email == email.strip().casefold(), User.password_hash.is_not(None)
            )
        )
        if member is None:
            raise WorkspaceError(404, "No account exists with that email")
        db.execute(
            insert(ProjectMember)
            .values(project_id=project.id, user_id=member.id)
            .on_conflict_do_nothing()
        )
        project_event(db, project)
        return person(member)


def remove_member(user: User, project_id: uuid.UUID, member_id: uuid.UUID) -> None:
    with SessionLocal.begin() as db:
        project = project_for(db, user, project_id, manage=True)
        db.execute(select(Project.id).where(Project.id == project.id).with_for_update())
        if member_id == project.owner_id:
            raise WorkspaceError(422, "Cannot remove project owner")
        member_link = db.get(ProjectMember, (project.id, member_id))
        if member_link is None:
            raise WorkspaceError(404, "Member not found in project")
        for task in db.scalars(
            select(Task)
            .where(Task.project_id == project.id, assigned_to(member_id))
            .with_for_update()
        ):
            set_assignees(
                db,
                task,
                [identifier for identifier in task.assignee_ids if identifier != member_id],
            )
            task.updated_at = now()
        db.delete(member_link)
        project_event(db, project)
        outbox(
            db,
            "event",
            f"project:{project.id}:{member_id}:{uuid.uuid4()}",
            user_id=str(member_id),
            task_id=str(project.id),
        )


def assignment(db: Session, task: Task, recipient_ids=None):
    if task.project_id:
        for identifier in task.assignee_ids if recipient_ids is None else recipient_ids:
            outbox(
                db,
                "assignment",
                f"assigned:{task.id}:{identifier}:{uuid.uuid4()}",
                task_id=str(task.id),
                recipient_id=str(identifier),
            )


def set_assignees(db: Session, task: Task, identifiers):
    identifiers = list(dict.fromkeys(identifiers))
    task.assignee_id = identifiers[0] if identifiers else None
    task.additional_assignees = [db.get(User, identifier) for identifier in identifiers[1:]]


def assignment_ids(user, project_id, assignee_id, assignee_ids):
    if assignee_ids is not None:
        identifiers = list(dict.fromkeys(assignee_ids))
        if assignee_id is not None and (not identifiers or identifiers[0] != assignee_id):
            raise WorkspaceError(422, "Use assignee_ids to select task assignees")
    else:
        identifiers = [assignee_id] if assignee_id else []
    return identifiers or ([user.id] if project_id is None else [])


def lock_membership(db: Session, project_id):
    """Hold the project row so remove_member cannot commit between check and assignment."""
    if project_id is not None:
        db.execute(select(Project.id).where(Project.id == project_id).with_for_update(read=True))


def validate_assignment(db: Session, user: User, project_id, assignee_id):
    if project_id is None:
        if assignee_id not in {None, user.id}:
            raise WorkspaceError(422, "Assign other people through a project")
    elif assignee_id is not None and db.get(ProjectMember, (project_id, assignee_id)) is None:
        raise WorkspaceError(422, "Add this person to the project before assigning a task")


def validate_due(value):
    if value is None:
        return
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise WorkspaceError(422, "Deadline must include a timezone")
    # Bound the UTC instant: extreme offsets near year 1 or 9999 would be stored
    # successfully but could never be read back, breaking every list containing them.
    try:
        year = value.astimezone(UTC).year
    except OverflowError:
        year = None
    if year is None or not 1970 <= year <= 2100:
        raise WorkspaceError(422, "Deadline must be between 1970 and 2100")


def create_task(
    user: User,
    text: str,
    status="pending",
    project_id=None,
    assignee_id=None,
    due_at=None,
    source="dashboard",
    chat_id=None,
    message_id=None,
    *,
    title: str | None = None,
    assignee_ids=None,
) -> dict:
    if not text.strip() or len(text) > 10000:
        raise WorkspaceError(422, "Task text must contain 1–10,000 characters")
    if status not in {"pending", "in_progress", "completed"}:
        raise WorkspaceError(422, "Invalid status")
    if title is not None and (not title.strip() or len(title.strip()) > 120):
        raise WorkspaceError(422, "Task title must contain 1–120 characters")
    validate_due(due_at)
    with SessionLocal.begin() as db:
        if project_id is not None:
            project_for(db, user, project_id, manage=True)
        identifiers = assignment_ids(user, project_id, assignee_id, assignee_ids)
        lock_membership(db, project_id)
        for identifier in identifiers:
            validate_assignment(db, user, project_id, identifier)
        if chat_id is not None:
            previous = db.scalar(
                select(Task).where(Task.chat_id == chat_id, Task.message_id == message_id)
            )
            if previous:
                if previous.user_id != user.id:
                    raise WorkspaceError(404, "Task not found")
                return task_data(db, previous, user)
        task = Task(
            user_id=user.id,
            text=text,
            title=title.strip() if title is not None else derive_title(text),
            source=source,
            status=status,
            project_id=project_id,
            assignee_id=identifiers[0] if identifiers else None,
            due_at=due_at,
            chat_id=chat_id,
            message_id=message_id,
        )
        set_assignees(db, task, identifiers)
        db.add(task)
        db.flush()
        task_event(db, task)
        assignment(db, task)
        return task_data(db, task, user)


def validated_task_fields(fields: dict) -> dict:
    if "status" in fields and fields["status"] not in {"pending", "in_progress", "completed"}:
        raise WorkspaceError(422, "Invalid status")
    if "text" in fields and (
        not fields["text"] or not fields["text"].strip() or len(fields["text"]) > 10000
    ):
        raise WorkspaceError(422, "Task text must contain 1–10,000 characters")
    if "title" in fields:
        if (
            not isinstance(fields["title"], str)
            or not fields["title"].strip()
            or len(fields["title"].strip()) > 120
        ):
            raise WorkspaceError(422, "Task title must contain 1–120 characters")
        fields = {**fields, "title": fields["title"].strip()}
    if "due_at" in fields:
        validate_due(fields["due_at"])
    return fields


def set_task_archived(db: Session, user: User, task: Task, managed: bool, fields: dict) -> dict:
    if not (managed or user.id in task.assignee_ids):
        raise WorkspaceError(403, "Only the manager or assignee can archive or restore this task")
    if set(fields) != {"archived"} or not isinstance(fields["archived"], bool):
        raise WorkspaceError(422, "Change archival separately from other task fields")
    if fields["archived"] and task.status != "completed":
        raise WorkspaceError(422, "Only completed tasks can be archived")
    task.archived_at = (task.archived_at or now()) if fields["archived"] else None
    task.updated_at = now()
    db.flush()
    task_event(db, task)
    return task_data(db, task, user)


def update_task(user: User, identifier: uuid.UUID, fields: dict) -> dict:
    allowed = {
        "status",
        "text",
        "title",
        "project_id",
        "assignee_id",
        "assignee_ids",
        "due_at",
        "archived",
    }
    if not fields or not set(fields) <= allowed:
        raise WorkspaceError(422, "Invalid task changes")
    with SessionLocal.begin() as db:
        # Same lock order as remove_member (project, then task) to avoid deadlocks.
        lock_membership(db, db.scalar(select(Task.project_id).where(Task.id == identifier)))
        task = task_for(db, user, identifier, lock=True)
        ensure_project_active(db, task)
        managed = can_manage(db, user, task)
        if "archived" in fields:
            return set_task_archived(db, user, task, managed, fields)
        if task.archived_at is not None:
            raise WorkspaceError(409, "Restore this task before editing it")
        if not managed and (set(fields) != {"status"} or not can_move(db, user, task)):
            raise WorkspaceError(403, "Only the manager can edit assignments and deadlines")
        fields = validated_task_fields(fields)
        old_project = db.get(Project, task.project_id) if task.project_id else None
        old_assignees = set(task.assignee_ids)
        project_id = fields.get("project_id", task.project_id)
        identifiers = (
            assignment_ids(user, project_id, fields.get("assignee_id"), fields.get("assignee_ids"))
            if "assignee_id" in fields or "assignee_ids" in fields
            else task.assignee_ids
        )
        if project_id is not None:
            if project_id != task.project_id:
                project_for(db, user, project_id, manage=True)
                lock_membership(db, project_id)
        for identifier in identifiers:
            validate_assignment(db, user, project_id, identifier)
        for key, value in fields.items():
            if key not in {"assignee_id", "assignee_ids"}:
                setattr(task, key, value)
        if project_id is None and task.user_id != user.id:
            # A personal task is visible only to its owner, so whoever moves a
            # project task out of the project takes ownership of it.
            task.user_id = user.id
        if "assignee_id" in fields or "assignee_ids" in fields:
            set_assignees(db, task, identifiers)
        task.updated_at = now()
        db.flush()
        task_event(db, task)
        if old_project and old_project.id != project_id:
            project_event(db, old_project)
        assignment(db, task, set(task.assignee_ids) - old_assignees)
        return task_data(db, task, user)


def delete_task(user: User, identifier: uuid.UUID):
    with SessionLocal.begin() as db:
        task = task_for(db, user, identifier, lock=True)
        ensure_project_active(db, task)
        if not can_manage(db, user, task):
            raise WorkspaceError(403, "Only the project manager can delete this task")
        task.deleted_at = task.updated_at = now()
        task_event(db, task)


def get_task(user: User, identifier: uuid.UUID) -> dict:
    from sqlalchemy.orm import aliased

    AssigneeModel = aliased(User, name="assignee_alias")
    with SessionLocal() as db:
        row = db.execute(
            select(Task, Project, AssigneeModel)
            .outerjoin(Project, Project.id == Task.project_id)
            .outerjoin(AssigneeModel, AssigneeModel.id == Task.assignee_id)
            .where(Task.id == identifier, Task.deleted_at.is_(None), task_scope(user))
        ).one_or_none()
        if row is None:
            raise WorkspaceError(404, "Task not found")
        task, project, assignee = row
        return _task_row_to_dict(user, task, assignee, project)


def comments(user: User, identifier: uuid.UUID) -> list[dict]:
    with SessionLocal() as db:
        task_for(db, user, identifier)
        return [
            {"id": c.id, "text": c.text, "author": person(author), "created_at": c.created_at}
            for c, author in db.execute(
                select(TaskComment, User)
                .join(User, User.id == TaskComment.author_id)
                .where(TaskComment.task_id == identifier)
                .order_by(TaskComment.created_at, TaskComment.id)
            )
        ]


def add_comment(
    user: User, identifier: uuid.UUID, text: str, chat_id=None, message_id=None
) -> dict:
    with SessionLocal.begin() as db:
        comment = save_comment(db, user, identifier, text, chat_id, message_id)
        return {
            "id": comment.id,
            "text": comment.text,
            "author": person(user),
            "created_at": comment.created_at,
        }


def save_comment(
    db: Session, user: User, identifier: uuid.UUID, text: str, chat_id=None, message_id=None
) -> TaskComment:
    """Save with the caller's transaction, including permission checks and live events."""
    if not text.strip() or len(text) > 4000:
        raise WorkspaceError(422, "Comments must contain 1–4,000 characters")
    task = task_for(db, user, identifier, lock=True)
    ensure_project_active(db, task)
    if task.archived_at is not None:
        raise WorkspaceError(409, "Restore this task before adding comments")
    comment = db.execute(
        insert(TaskComment)
        .values(
            task_id=task.id,
            author_id=user.id,
            text=text,
            chat_id=chat_id,
            message_id=message_id,
        )
        .on_conflict_do_nothing(constraint="uq_comment_message")
        .returning(TaskComment)
    ).scalar_one_or_none()
    if comment is None:
        comment = db.scalar(
            select(TaskComment).where(
                TaskComment.chat_id == chat_id, TaskComment.message_id == message_id
            )
        )
        if comment.author_id != user.id or comment.task_id != identifier:
            raise WorkspaceError(404, "Comment not found")
    else:
        task_event(db, task)
    return comment
