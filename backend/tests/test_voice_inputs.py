"""Spoken answers use the real Redis FSM and worker, with mocked Telegram/audio transport."""

import os
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

import pytest
from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import Audio, CallbackQuery, Chat, Message, Voice
from aiogram.types import User as TelegramUser
from sqlalchemy import func, select

from app import workspace as ws
from app.auth import telegram_user
from app.db import SessionLocal
from app.domain import retry_voice
from app.jobs import transcribe
from app.models import Outbox, Project, ProjectMember, Task, VoiceJob, now
from app.project_bot import WorkFlow, callback, conversation
from app.project_bot.wizard import ask_project_name
from app.transcription import PermanentVoiceError
from app.voice_input import VOICE_ANSWER, spoken_deadline, spoken_email

TOKEN = "123456:synthetic_token_for_tests"


@pytest.fixture
async def form():
    storage = RedisStorage.from_url(os.environ["REDIS_URL"], state_ttl=300, data_ttl=300)
    session = AsyncMock(return_value=SimpleNamespace())
    bot = Bot(TOKEN, session=session)
    state = FSMContext(storage, StorageKey(bot_id=123456, chat_id=100, user_id=100))
    yield SimpleNamespace(state=state, session=session, bot=bot, account=telegram_user(100))
    await storage.close()


def message(form, identifier=700, text=None, media=None):
    return Message(
        message_id=identifier,
        date=now(),
        chat=Chat(id=100, type="private"),
        from_user=TelegramUser(id=100, is_bot=False, first_name="Manager"),
        text=text,
        **(media or {}),
    ).as_(form.bot)


def press(form, data, user_id=100):
    return CallbackQuery(
        id=data,
        chat_instance="test",
        data=data,
        from_user=TelegramUser(id=user_id, is_bot=False, first_name="Person"),
        message=Message(
            message_id=701,
            date=now(),
            chat=Chat(id=100, type="private"),
            from_user=TelegramUser(id=123456, is_bot=True, first_name="Bot"),
            text="Spoken answer",
        ).as_(form.bot),
    ).as_(form.bot)


async def record(form, identifier=700, media_type="voice", duration=4, size=1000):
    media_class = Voice if media_type == "voice" else Audio
    media = {
        media_type: media_class(
            file_id=f"recording-{identifier}",
            file_unique_id=f"unique-{identifier}",
            duration=duration,
            file_size=size,
        )
    }
    await conversation(message(form, identifier, media=media), form.account, form.state)
    with SessionLocal() as db:
        return db.scalar(select(VoiceJob).where(VoiceJob.message_id == identifier))


def finish(monkeypatch, identifier, transcript):
    monkeypatch.setattr("app.jobs.download_voice", Mock())
    convert = Mock(return_value=transcript)
    monkeypatch.setattr("app.jobs.transcribe_audio", convert)
    transcribe.run(str(identifier), 0)
    return convert


async def use_answer(form, action="approve", account=None):
    pending = (await form.state.get_data())[VOICE_ANSWER]
    await callback(
        press(
            form,
            f"voiceinput:{pending['token']}:{action}"
            + (f":{pending.get('revision', 0)}" if action == "approve" else ""),
        ),
        account or form.account,
        form.state,
    )
    return pending["token"]


def confirmations():
    with SessionLocal() as db:
        return list(
            db.scalars(select(Outbox).where(Outbox.dedupe_key.startswith("voice-confirm:")))
        )


@pytest.mark.parametrize("media_type", ["voice", "audio"])
async def test_voice_project_name_is_confirmed_and_created_once(monkeypatch, form, media_type):
    await ask_project_name(message(form), form.account, form.state)
    assert "text or a voice note" in form.session.call_args.args[1].text
    job = await record(form, media_type=media_type)
    assert job.kind == "input" and job.status == "queued"
    assert "transcribing your project name" in form.session.call_args.args[1].text
    finish(monkeypatch, job.id, "Նոր կայք & <Launch>.")
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Project)) == 0
        assert db.scalar(select(func.count()).select_from(Task)) == 0
    [notice] = confirmations()
    assert "Նոր կայք &amp; &lt;Launch&gt;" in notice.payload["text"]
    assert len(notice.payload["text"]) < 4096
    token = await use_answer(form)
    await callback(press(form, f"voiceinput:{token}:use"), form.account, form.state)
    assert await form.state.get_state() is None
    with SessionLocal() as db:
        projects = list(db.scalars(select(Project)))
        assert len(projects) == 1
        assert projects[0].name == "Նոր կայք & <Launch>" and projects[0].owner_id == form.account.id
        assert db.get(VoiceJob, job.id).status == "completed"
    assert finish(monkeypatch, job.id, "Duplicate").call_count == 0
    assert len(confirmations()) == 1


@pytest.mark.parametrize("choice", ["Personal task", "Launch"])
async def test_voice_project_selection_advances_to_title(monkeypatch, form, choice):
    project = ws.create_project(form.account, "Launch")
    await form.state.set_state(WorkFlow.task_project)
    job = await record(form)
    finish(monkeypatch, job.id, choice)
    await use_answer(form)
    assert await form.state.get_state() == WorkFlow.task_title.state
    assert (await form.state.get_data()).get("project_id") == (
        str(project["id"]) if choice == "Launch" else None
    )


async def test_voice_member_email_is_normalized_and_validated(monkeypatch, form):
    project = ws.create_project(form.account, "People")
    developer = telegram_user(200)
    await form.state.set_state(WorkFlow.member_email)
    await form.state.update_data(project_id=str(project["id"]))
    job = await record(form)
    finish(monkeypatch, job.id, "test two at hire dot me.")
    assert (await form.state.get_data())[VOICE_ANSWER]["text"] == "test2@hire.me"
    await use_answer(form)
    with SessionLocal() as db:
        assert db.get(ProjectMember, (project["id"], developer.id)) is not None
        assert db.scalar(select(func.count()).select_from(Task)) == 0


@pytest.mark.parametrize("answer", ["Jamie", "test two at hire dot me", "unassigned"])
async def test_voice_assignee_retains_draft_and_opens_deadline(monkeypatch, form, answer):
    project = ws.create_project(form.account, "Assignments")
    developer = telegram_user(200)
    ws.add_member(form.account, project["id"], developer.email)
    await form.state.set_state(WorkFlow.task_assignee)
    await form.state.update_data(
        project_id=str(project["id"]), title="Headline", text="Details", chat_id=100, message_id=650
    )
    job = await record(form)
    finish(monkeypatch, job.id, answer)
    await use_answer(form)
    assert await form.state.get_state() == WorkFlow.task_due.state
    data = await form.state.get_data()
    assert data["title"] == "Headline" and data["text"] == "Details"
    assert data["assignee_id"] == (None if answer == "unassigned" else str(developer.id))
    await conversation(message(form, 702, text="2028-02-29 18:00"), form.account, form.state)
    with SessionLocal() as db:
        task = db.scalar(select(Task))
        assert task.project_id == project["id"] and task.title == "Headline"
        assert task.message_id == 650
        assert task.assignee_id == (None if answer == "unassigned" else developer.id)


@pytest.mark.parametrize("answer", ["tomorrow at six pm", "no deadline"])
async def test_spoken_deadline_creates_personal_task(monkeypatch, form, answer):
    await form.state.set_state(WorkFlow.task_due)
    await form.state.update_data(title="Headline", text="Details", chat_id=100, message_id=650)
    job = await record(form)
    finish(monkeypatch, job.id, answer)
    await use_answer(form)
    with SessionLocal() as db:
        task = db.scalar(select(Task))
        assert task.title == "Headline" and task.text == "Details" and task.message_id == 650
        assert task.user_id == form.account.id and task.project_id is None
        if answer == "no deadline":
            assert task.due_at is None
        else:
            expected = (now().astimezone(ZoneInfo("Asia/Yerevan")) + timedelta(days=1)).replace(
                hour=18, minute=0, second=0, microsecond=0
            )
            assert task.due_at == expected


async def test_spoken_edit_deadline_updates_existing_task(monkeypatch, form):
    task = ws.create_task(form.account, "Existing", due_at=now() + timedelta(days=1))
    await form.state.set_state(WorkFlow.edit_due)
    await form.state.update_data(edit_task=str(task["id"]))
    job = await record(form)
    finish(monkeypatch, job.id, "no deadline")
    await use_answer(form)
    with SessionLocal() as db:
        assert db.get(Task, task["id"]).due_at is None
        assert db.scalar(select(func.count()).select_from(Task)) == 1


@pytest.mark.parametrize(
    "change", ["cancel", "new_question", "same_question", "typed", "new_voice"]
)
async def test_old_recording_cannot_answer_a_different_question(monkeypatch, form, change):
    await form.state.set_state(WorkFlow.project_name)
    job = await record(form)
    if change == "typed":
        await conversation(message(form, 701, text="Typed project"), form.account, form.state)
    elif change == "new_voice":
        await record(form, identifier=702)
    else:
        await form.state.clear()
        if change == "new_question":
            await form.state.set_state(WorkFlow.member_email)
        elif change == "same_question":
            await form.state.set_state(WorkFlow.project_name)
    finish(monkeypatch, job.id, "Old recording")
    assert confirmations() == []
    with SessionLocal() as db:
        names = list(db.scalars(select(Project.name)))
        assert names == (["Typed project"] if change == "typed" else [])
        assert db.get(VoiceJob, job.id).status == "completed"


async def test_legacy_record_again_allows_new_recording_and_typing(monkeypatch, form):
    await form.state.set_state(WorkFlow.project_name)
    job = await record(form)
    finish(monkeypatch, job.id, "First name")
    token = await use_answer(form, "again")
    assert await form.state.get_state() == WorkFlow.project_name.state
    replacement = await record(form, identifier=702)
    assert replacement is not None
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Project)) == 0
    await conversation(message(form, 703, text="Corrected name"), form.account, form.state)
    await callback(press(form, f"voiceinput:{token}:use"), form.account, form.state)
    with SessionLocal() as db:
        assert list(db.scalars(select(Project.name))) == ["Corrected name"]
    finish(monkeypatch, replacement.id, "Stale recording")
    with SessionLocal() as db:
        assert list(db.scalars(select(Project.name))) == ["Corrected name"]


@pytest.mark.parametrize("action", ["use", "approve", "cancel"])
async def test_confirmation_cannot_be_used_by_another_account(monkeypatch, form, action):
    await form.state.set_state(WorkFlow.project_name)
    job = await record(form)
    finish(monkeypatch, job.id, "Private")
    pending = (await form.state.get_data())[VOICE_ANSWER]
    await callback(
        press(
            form,
            f"voiceinput:{pending['token']}:{action}" + ("" if action == "use" else ":0"),
            user_id=200,
        ),
        telegram_user(200),
        form.state,
    )
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Project)) == 0
    assert (await form.state.get_data())[VOICE_ANSWER] == pending


async def test_voice_project_creation_rechecks_role(monkeypatch, form):
    await form.state.set_state(WorkFlow.project_name)
    job = await record(form)
    finish(monkeypatch, job.id, "Unauthorized")
    form.account.role = "developer"
    with pytest.raises(ws.WorkspaceError, match="Only project managers"):
        await use_answer(form)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Project)) == 0


@pytest.mark.parametrize("duration,size", [(301, 1000), (5, 21 * 1024 * 1024)])
async def test_oversized_input_does_not_queue_a_job(form, duration, size):
    await form.state.set_state(WorkFlow.project_name)
    assert await record(form, duration=duration, size=size) is None
    assert "5 minutes and 20 MB" in form.session.call_args.args[1].text
    assert await form.state.get_state() == WorkFlow.project_name.state


async def test_failure_allows_retry_without_creating_a_task(monkeypatch, form):
    await form.state.set_state(WorkFlow.project_name)
    job = await record(form)
    monkeypatch.setattr(
        "app.jobs.download_voice", Mock(side_effect=PermanentVoiceError("Bad audio"))
    )
    transcribe.run(str(job.id), 0)
    assert await form.state.get_state() == WorkFlow.project_name.state
    assert retry_voice(job.id, 100)
    monkeypatch.setattr("app.jobs.download_voice", Mock())
    monkeypatch.setattr("app.jobs.transcribe_audio", Mock(return_value="Recovered name"))
    transcribe.run(str(job.id), 1)
    await use_answer(form)
    with SessionLocal() as db:
        assert list(db.scalars(select(Project.name))) == ["Recovered name"]
        assert db.scalar(select(func.count()).select_from(Task)) == 0


@pytest.mark.parametrize(
    "value,expected",
    [
        ("test two at hire dot me.", "test2@hire.me"),
        ("a underscore b at example dot com", "a_b@example.com"),
    ],
)
def test_spoken_email_symbols(value, expected):
    assert spoken_email(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("tomorrow at six pm", "2026-10-23 18:00"),
        ("today at six thirty pm", "2026-10-22 18:30"),
        ("today at twelve am", "2026-10-22 00:00"),
        ("today at 18:25", "2026-10-22 18:25"),
        ("next friday at nine am", "2026-10-23 09:00"),
        ("October 23rd 2026 at five pm", "2026-10-23 17:00"),
        ("in two days", "2026-10-24 23:59"),
        ("no deadline", "-"),
        ("2028-02-29 18:30", "2028-02-29 18:30"),
        ("tomorrow at 25:00", "tomorrow at 25:00"),
        ("unknown date", "unknown date"),
    ],
)
def test_spoken_dates_are_converted_without_guessing_invalid_times(value, expected):
    reference = datetime(2026, 10, 22, 12, tzinfo=ZoneInfo("Asia/Yerevan"))
    assert spoken_deadline(value, reference) == expected
