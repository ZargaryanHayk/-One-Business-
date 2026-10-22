from datetime import timedelta
from html.parser import HTMLParser
from types import SimpleNamespace

from app.bot_ui import tasks_text
from app.models import now
from app.telegram_format import deadline_label, task_details


class TextCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = ""
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)

    def handle_data(self, data):
        self.text += data


def task(**changes):
    return {
        "id": "task-1",
        "title": "Release review <headline>",
        "text": "Review the release <script>alert(1)</script> & plan",
        "project_name": "Launch <b>not markup</b>",
        "status": "in_progress",
        "assignee": {"name": "Test Two & Team", "email": "test2@hire.me"},
        "due_at": now() + timedelta(days=2),
        "created_at": now(),
        "source": "dashboard",
        **changes,
    }


def test_task_html_escapes_user_content_and_separates_metadata():
    result = task_details(task(), [{"author": {"name": "<i>Alex</i>"}, "text": "<a>Link</a>"}])
    parsed = TextCollector()
    parsed.feed(result)
    assert set(parsed.tags) == {"b"}
    assert "Task\nRelease review <headline>" in parsed.text
    assert "Description\nReview the release <script>alert(1)</script> & plan" in parsed.text
    assert "Project: Launch <b>not markup</b>" in parsed.text
    assert "Status: In progress" in parsed.text
    assert "Assigned to: Test Two & Team" in parsed.text
    assert "Source: 🌐 Website" in parsed.text
    assert "<i>Alex</i>: <a>Link</a>" in parsed.text


def test_long_emoji_messages_and_comments_stay_within_telegram_limit():
    huge = "🧑🏽‍💻<&>" * 4000
    data = task(title=huge, text=huge, project_name=huge, assignee={"name": huge, "email": huge})
    comments = [{"author": {"name": huge}, "text": huge}] * 3
    for rendered in (task_details(data, comments), tasks_text([SimpleNamespace(**data)] * 5, 0)):
        parsed = TextCollector()
        parsed.feed(rendered)
        assert len(parsed.text.encode("utf-16-le")) // 2 <= 4096
        assert set(parsed.tags) == {"b"}
        assert "…" in parsed.text


def test_due_labels_distinguish_no_deadline_overdue_and_completed():
    past = now() - timedelta(days=2)
    assert deadline_label(None, "pending") == "No deadline"
    assert "⚠️ Overdue" in deadline_label(past, "pending")
    assert "Overdue" not in deadline_label(past, "completed")
    assert "⏰ Due soon" in deadline_label(now() + timedelta(days=2), "in_progress")
