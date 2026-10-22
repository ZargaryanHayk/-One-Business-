"""Transcripts wait for Cancel/Approve; cancelling lets the same field be answered again."""

import json
from urllib.parse import urlparse

import httpx
import pytest
from sqlalchemy import delete, func, select
from test_voice_inputs import finish, message, press, record
from test_voice_inputs import form as form

from app import workspace as ws
from app.config import settings
from app.db import SessionLocal
from app.jobs import notify
from app.models import Outbox, Project, Task, TaskComment, TelegramSession, VoiceJob
from app.project_bot import WorkFlow, callback, conversation
from app.voice_input import INPUT_STATES, VOICE_ANSWER, editor_origin


def headers(pending):
    return {"X-Voice-Edit-Token": pending["editor_token"]}


async def approve(form, pending):
    await callback(
        press(form, f"voiceinput:{pending['token']}:approve:{pending.get('revision', 0)}"),
        form.account,
        form.state,
    )


async def cancel(form, pending):
    await callback(
        press(form, f"voiceinput:{pending['token']}:cancel:{pending.get('revision', 0)}"),
        form.account,
        form.state,
    )


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        ("http://localhost:8080", "http://127.0.0.1:8080"),
        ("http://localhost:9090", "http://127.0.0.1:9090"),
        ("http://localhost", "http://127.0.0.1"),
        ("https://localhost:8443", "https://127.0.0.1:8443"),
        ("https://tasks.example.com", "https://tasks.example.com"),
        ("http://127.0.0.1:8080", "http://127.0.0.1:8080"),
        ("http://[::1]:8080", "http://[::1]:8080"),
    ],
)
def test_legacy_editor_origin(monkeypatch, origin, expected):
    monkeypatch.setattr(settings(), "app_origin", origin)
    assert editor_origin() == expected


async def test_local_voice_review_delivers_without_web_links(monkeypatch, form):
    monkeypatch.setattr(settings(), "app_origin", "http://localhost:8080")
    await form.state.set_state(WorkFlow.task_content)
    await form.state.update_data(title="Headline")
    job = await record(form)
    finish(monkeypatch, job.id, "Spoken description")
    pending = (await form.state.get_data())[VOICE_ANSWER]
    with SessionLocal() as db:
        notification = db.scalar(
            select(Outbox).where(Outbox.dedupe_key == f"voice-confirm:{job.id}")
        )
    sent = []

    def telegram(request):
        payload = json.loads(request.content)
        # Mirror Telegram's URL-host check, which rejected localhost and hid the reply.
        for row in payload["reply_markup"]["inline_keyboard"]:
            for button in row:
                if "url" in button and "." not in urlparse(button["url"]).hostname:
                    return httpx.Response(
                        400, json={"ok": False, "description": "Bad Request: Wrong HTTP URL"}
                    )
        sent.append(payload)
        return httpx.Response(200, json={"ok": True, "result": {}})

    with httpx.Client(transport=httpx.MockTransport(telegram)) as transport:
        monkeypatch.setattr("app.jobs.httpx.post", transport.post)
        notify.run(**notification.payload)
    assert len(sent) == 1
    assert "Spoken description" in sent[0]["text"]
    [cancel_button, approve_button] = sent[0]["reply_markup"]["inline_keyboard"][0]
    assert cancel_button["text"] == "Cancel"
    assert cancel_button["callback_data"] == f"voiceinput:{pending['token']}:cancel:0"
    assert approve_button["text"] == "Approve"
    assert all("url" not in button for button in (cancel_button, approve_button))
    await approve(form, pending)
    assert await form.state.get_state() == WorkFlow.task_due.state
    assert (await form.state.get_data())["text"] == "Spoken description"


@pytest.mark.parametrize(
    "field", ["title", "description", "comment", "edit_title", "edit_text", "quick"]
)
async def test_every_voice_field_waits_for_approval(monkeypatch, form, field):
    task = (
        ws.create_task(form.account, "Original")
        if field in {"comment", "edit_title", "edit_text"}
        else None
    )
    states = {
        "title": WorkFlow.task_title,
        "description": WorkFlow.task_content,
        "comment": WorkFlow.comment,
        "edit_title": WorkFlow.edit_title,
        "edit_text": WorkFlow.edit_text,
        "quick": WorkFlow.quick_voice,
    }
    await form.state.set_state(states[field])
    if task:
        await form.state.update_data(
            **{"task_id" if field == "comment" else "edit_task": str(task["id"])}
        )
    if field == "description":
        await form.state.update_data(title="Typed title")
    job = await record(form)
    assert job.kind == "input"
    finish(monkeypatch, job.id, "Reviewed words")
    pending = (await form.state.get_data())[VOICE_ANSWER]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == (1 if task else 0)
        assert db.scalar(select(func.count()).select_from(TaskComment)) == 0
        if task:
            assert db.get(Task, task["id"]).text == "Original"
    await approve(form, pending)
    with SessionLocal() as db:
        if field == "quick":
            saved = db.scalar(select(Task))
            assert saved.text == "Reviewed words" and saved.source == "voice"
            assert saved.message_id == 700
        elif field == "comment":
            assert db.scalar(select(TaskComment)).text == "Reviewed words"
        elif field in {"edit_title", "edit_text"}:
            saved = db.get(Task, task["id"])
            assert getattr(saved, "title" if field == "edit_title" else "text") == "Reviewed words"
        elif field == "title":
            assert await form.state.get_state() == WorkFlow.task_content.state
        else:
            assert await form.state.get_state() == WorkFlow.task_due.state
            assert (await form.state.get_data())["text"] == "Reviewed words"


async def test_legacy_editor_is_prefilled_and_correction_requires_latest_approval(
    monkeypatch, form, client
):
    await form.state.set_state(WorkFlow.project_name)
    job = await record(form)
    finish(monkeypatch, job.id, "Original name")
    original = (await form.state.get_data())[VOICE_ANSWER]
    with SessionLocal() as db:
        notification = db.scalar(
            select(Outbox).where(Outbox.dedupe_key == f"voice-confirm:{job.id}")
        )
    [row] = notification.payload["reply_markup"]["inline_keyboard"]
    assert [button["text"] for button in row] == ["Cancel", "Approve"]
    assert all("url" not in button for button in row)
    preview = client.get("/api/voice-review", headers=headers(original))
    assert preview.status_code == 200 and preview.json()["text"] == "Original name"
    assert preview.headers["cache-control"] == "no-store"
    body = {"text": "Corrected name", "revision": 0}
    response = client.post("/api/voice-review", headers=headers(original), json=body)
    assert response.status_code == 200 and response.json()["revision"] == 1
    # Retrying a save does not create duplicate bot notifications.
    assert client.post("/api/voice-review", headers=headers(original), json=body).status_code == 200
    assert (
        client.post(
            "/api/voice-review", headers=headers(original), json={"text": "Stale", "revision": 0}
        ).status_code
        == 409
    )
    await approve(form, original)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Project)) == 0
        assert (
            db.scalar(
                select(func.count())
                .select_from(Outbox)
                .where(Outbox.dedupe_key.startswith("voice-edited:"))
            )
            == 1
        )
    corrected = (await form.state.get_data())[VOICE_ANSWER]
    await cancel(form, original)
    assert (await form.state.get_data())[VOICE_ANSWER] == corrected
    await approve(form, corrected)
    await approve(form, corrected)
    with SessionLocal() as db:
        assert list(db.scalars(select(Project.name))) == ["Corrected name"]
    assert client.get("/api/voice-review", headers=headers(original)).status_code == 410


async def test_answers_wait_for_cancel_before_replacement(monkeypatch, form):
    await form.state.set_state(WorkFlow.project_name)
    job = await record(form)
    finish(monkeypatch, job.id, "First name")
    before = (await form.state.get_data())[VOICE_ANSWER]
    await record(form, identifier=702)
    assert "tap Cancel" in form.session.call_args.args[1].text
    assert (await form.state.get_data())[VOICE_ANSWER] == before
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(VoiceJob)) == 1
    await conversation(message(form, 703, text="Typed correction"), form.account, form.state)
    assert (await form.state.get_data())[VOICE_ANSWER] == before
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Project)) == 0
    await cancel(form, before)
    await conversation(message(form, 704, text="Typed correction"), form.account, form.state)
    with SessionLocal() as db:
        assert list(db.scalars(select(Project.name))) == ["Typed correction"]


@pytest.mark.parametrize("field", list(INPUT_STATES))
async def test_cancel_preserves_question_and_draft_and_invalidates_old_buttons(
    monkeypatch, form, client, field
):
    task = ws.create_task(form.account, "Original description")
    draft = {
        "title": "Chosen title",
        "project_id": "selected-project",
        "assignee_id": "selected-assignee",
        "task_id": str(task["id"]),
        "edit_task": str(task["id"]),
    }
    await form.state.set_state(field)
    await form.state.set_data(draft)
    job = await record(form)
    finish(monkeypatch, job.id, "Rejected words")
    pending = (await form.state.get_data())[VOICE_ANSWER]
    await cancel(form, pending)
    assert await form.state.get_state() == field
    data = await form.state.get_data()
    assert not data.get(VOICE_ANSWER)
    assert all(data[key] == value for key, value in draft.items())
    assert "voice note or type it" in form.session.call_args.args[1].text
    assert client.get("/api/voice-review", headers=headers(pending)).status_code == 410
    await cancel(form, pending)
    await approve(form, pending)
    assert await form.state.get_state() == field
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 1
        assert db.get(Task, task["id"]).text == "Original description"
        assert db.scalar(select(func.count()).select_from(Project)) == 0
        assert db.scalar(select(func.count()).select_from(TaskComment)) == 0


@pytest.mark.parametrize("replacement", ["voice", "audio", "text"])
async def test_cancel_allows_replacement_description_before_deadline(
    monkeypatch, form, replacement
):
    await form.state.set_state(WorkFlow.task_content)
    await form.state.update_data(title="Keep this title")
    job = await record(form)
    finish(monkeypatch, job.id, "Discard this description")
    original = (await form.state.get_data())[VOICE_ANSWER]
    await cancel(form, original)
    if replacement in {"voice", "audio"}:
        next_job = await record(form, identifier=702, media_type=replacement)
        finish(monkeypatch, next_job.id, "Replacement description")
        pending = (await form.state.get_data())[VOICE_ANSWER]
        # Neither stale action may cancel or approve the replacement recording.
        await cancel(form, original)
        await approve(form, original)
        assert (await form.state.get_data())[VOICE_ANSWER] == pending
        await approve(form, pending)
    else:
        await conversation(
            message(form, 703, text="Replacement description"), form.account, form.state
        )
    assert await form.state.get_state() == WorkFlow.task_due.state
    data = await form.state.get_data()
    assert data["title"] == "Keep this title"
    assert data["text"] == "Replacement description"
    assert data["source"] == ("text" if replacement == "text" else "voice")
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 0


async def test_cancel_quick_voice_then_type_creates_text_task(monkeypatch, form):
    await form.state.set_state(WorkFlow.quick_voice)
    job = await record(form)
    finish(monkeypatch, job.id, "Discard this task")
    pending = (await form.state.get_data())[VOICE_ANSWER]
    await cancel(form, pending)
    await conversation(message(form, 703, text="Typed replacement task"), form.account, form.state)
    with SessionLocal() as db:
        [task] = list(db.scalars(select(Task)))
        assert task.text == "Typed replacement task" and task.source == "text"
        assert task.message_id == 703


async def test_editor_rejects_audio_extra_fields_foreign_origin_and_revoked_session(
    monkeypatch, form, client
):
    await form.state.set_state(WorkFlow.project_name)
    job = await record(form)
    finish(monkeypatch, job.id, "Name")
    pending = (await form.state.get_data())[VOICE_ANSWER]
    request_headers = headers(pending)
    assert (
        client.post(
            "/api/voice-review",
            headers=request_headers,
            json={"text": "Name", "revision": 0, "audio": "recording"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/voice-review",
            headers={**request_headers, "Origin": "https://foreign.example"},
            json={"text": "Name", "revision": 0},
        ).status_code
        == 403
    )
    assert client.get("/api/voice-review").status_code == 410
    with SessionLocal.begin() as db:
        db.execute(delete(TelegramSession).where(TelegramSession.telegram_id == 100))
    assert client.get("/api/voice-review", headers=request_headers).status_code == 410


async def test_long_transcript_is_returned_in_full_before_approval(monkeypatch, form, client):
    await form.state.set_state(WorkFlow.task_content)
    await form.state.update_data(title="Headline")
    transcript = "Long transcript & <words>. " * 300
    job = await record(form)
    finish(monkeypatch, job.id, transcript)
    pending = (await form.state.get_data())[VOICE_ANSWER]
    assert (
        client.get("/api/voice-review", headers=headers(pending)).json()["text"]
        == transcript.strip()
    )
    with SessionLocal() as db:
        parts = list(
            db.scalars(
                select(Outbox).where(Outbox.dedupe_key.startswith(f"voice-confirm:{job.id}"))
            )
        )
        assert len(parts) == 3
        assert sum("reply_markup" in part.payload for part in parts) == 1
        assert db.scalar(select(func.count()).select_from(Task)) == 0


async def test_pending_recording_without_legacy_editor_token_still_completes(
    monkeypatch, form, client
):
    await form.state.set_state(WorkFlow.project_name)
    job = await record(form)
    data = await form.state.get_data()
    data[VOICE_ANSWER].pop("editor_token")
    await form.state.set_data(data)
    finish(monkeypatch, job.id, "Existing recording")
    pending = (await form.state.get_data())[VOICE_ANSWER]
    assert (
        client.get("/api/voice-review", headers=headers(pending)).json()["text"]
        == "Existing recording"
    )
