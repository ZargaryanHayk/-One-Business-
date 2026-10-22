from html.parser import HTMLParser
from types import SimpleNamespace

from app.bot_ui import tasks_text
from app.telegram_format import task_button, task_summary


class TextCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = ""
        self.tags = []

    def handle_data(self, data):
        self.text += data

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)


def test_long_descriptions_do_not_expand_task_lists():
    description = "Full description stays on the task.\n" * 300
    first = SimpleNamespace(title="A short headline", text=description, status="pending")
    second = SimpleNamespace(title="Another task", text="Other details", status="completed")
    rendered = tasks_text([first, second], 0)
    assert task_summary(first, 1) == "⏳ <b>1. A short headline</b>"
    assert "⏳ <b>1. A short headline</b>\n✅ <b>2. Another task</b>" in rendered
    assert "Full description" not in rendered and "Other details" not in rendered
    assert first.text == description


def test_long_multiline_titles_and_legacy_text_use_safe_single_line_previews():
    original = "<b>Headline</b>\n\t" + "🧑🏽‍💻" * 100
    for data in (
        {"title": original, "text": "Description", "status": "in_progress"},
        {"title": None, "text": original, "status": "in_progress"},
    ):
        summary = task_summary(data, 6)
        parsed = TextCollector()
        parsed.feed(summary)
        assert set(parsed.tags) == {"b"}
        assert "<b>Headline</b>" in parsed.text
        assert "\n" not in parsed.text and "\t" not in parsed.text
        assert parsed.text.endswith("…")
        assert len(parsed.text.encode("utf-16-le")) // 2 < 60
        assert "…" in task_button(data, 6)
