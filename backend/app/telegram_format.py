"""Safe, bounded HTML presentation for Telegram task messages."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from app.config import settings

STATUSES = {
    "pending": ("⏳", "Pending"),
    "in_progress": ("🔄", "In progress"),
    "completed": ("✅", "Completed"),
}
SOURCES = {"text": "💬 Telegram text", "voice": "🎙 Telegram voice", "dashboard": "🌐 Website"}


def field(task, key, default=None):
    return task.get(key, default) if isinstance(task, Mapping) else getattr(task, key, default)


def short(value, limit):
    encoded = str(value).encode("utf-16-le")
    if len(encoded) <= limit * 2:
        return str(value)
    return encoded[: (limit - 1) * 2].decode("utf-16-le", errors="ignore").rstrip() + "…"


def safe(value, limit):
    return escape(short(value, limit))


def status_label(status):
    icon, label = STATUSES[status]
    return f"{icon} {label}"


def date_label(value):
    return value.astimezone(ZoneInfo(settings().app_timezone)).strftime("%d %b %Y · %H:%M")


def deadline_label(value, status):
    if value is None:
        return "No deadline"
    label = date_label(value)
    if status != "completed":
        if value < datetime.now(UTC):
            label += " · ⚠️ Overdue"
        elif value <= datetime.now(UTC) + timedelta(days=4):
            label += " · ⏰ Due soon"
    return label


def task_summary(task, number):
    icon = STATUSES[field(task, "status")][0]
    title = field(task, "title") or field(task, "text")
    preview = " ".join(title.split())
    return f"{icon} <b>{number}. {safe(preview, 45)}</b>"


def task_button(task, number):
    icon = STATUSES[field(task, "status")][0]
    title = field(task, "title") or field(task, "text")
    return f"{icon} {number}. {short(' '.join(title.split()), 45)}"


def task_details(task, comments):
    people = task.get("assignees") or ([task["assignee"]] if task["assignee"] else [])
    assigned = safe(", ".join(person["name"] for person in people) or "Unassigned", 500)
    lines = [
        f"📝 <b>Task</b>\n{safe(task.get('title') or task['text'], 120)}",
        f"📄 <b>Description</b>\n{safe(task['text'], 2050)}",
        "\n".join(
            [
                f"📁 <b>Project:</b> {safe(task['project_name'] or 'Personal', 120)}",
                f"{STATUSES[task['status']][0]} <b>Status:</b> {STATUSES[task['status']][1]}",
                f"👤 <b>Assigned to:</b> {assigned}",
            ]
        ),
    ]
    emails = ", ".join(person["email"] for person in people if person.get("email"))
    if emails:
        lines[-1] += f"\n✉️ <b>Email:</b> {safe(emails, 254)}"
    lines.append(
        "\n".join(
            [
                f"📅 <b>Deadline:</b> {deadline_label(task['due_at'], task['status'])}",
                f"🌍 <b>Time zone:</b> {safe(settings().app_timezone, 50)}",
                f"📥 <b>Source:</b> {SOURCES[task['source']]}",
                f"🕒 <b>Created:</b> {date_label(task['created_at'])}",
            ]
        )
    )
    if comments:
        lines.append(
            "💬 <b>Recent comments</b>\n"
            + "\n".join(
                f"<b>{safe(comment['author']['name'], 40)}:</b> {safe(comment['text'], 150)}"
                for comment in comments[-3:]
            )
        )
    return "\n\n".join(lines)
