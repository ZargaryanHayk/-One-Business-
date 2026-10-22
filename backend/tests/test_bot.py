from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from app.auth import telegram_user
from app.bot import audio_message, voice_message


@pytest.fixture
def context():
    return telegram_user(100), FSMContext(
        MemoryStorage(), StorageKey(bot_id=123456, chat_id=100, user_id=100)
    )


def message(duration=10, size=1000):
    return SimpleNamespace(
        voice=SimpleNamespace(duration=duration, file_size=size, file_id="file"),
        answer=AsyncMock(),
        from_user=SimpleNamespace(id=100, full_name="Alex"),
        chat=SimpleNamespace(id=100),
        message_id=1,
    )


@pytest.mark.parametrize("duration,size", [(301, 1000), (10, 21 * 1024 * 1024)])
async def test_oversized_voice_rejected_before_queue(monkeypatch, duration, size, context):
    enqueue = Mock()
    monkeypatch.setattr("app.project_bot.messages.queue_voice_input", enqueue)
    await voice_message(message(duration, size), *context)
    assert not enqueue.called


async def test_voice_acknowledged_only_after_job_is_saved(monkeypatch, context):
    incoming = message()

    def enqueue(*args):
        assert incoming.answer.await_count == 0

    monkeypatch.setattr("app.project_bot.messages.queue_voice_input", enqueue)
    await voice_message(incoming, *context)
    assert incoming.answer.await_count == 1
    assert "Got it" in incoming.answer.await_args.args[0]


async def test_failed_voice_save_is_not_acknowledged(monkeypatch, context):
    incoming = message()
    monkeypatch.setattr(
        "app.project_bot.messages.queue_voice_input", Mock(side_effect=RuntimeError("offline"))
    )
    await voice_message(incoming, *context)
    assert incoming.answer.await_count == 1
    assert "Could not save" in incoming.answer.await_args.args[0]


@pytest.mark.parametrize("duration,size", [(301, 1000), (10, 21 * 1024 * 1024)])
async def test_oversized_audio_rejected_before_queue(monkeypatch, duration, size, context):
    enqueue = Mock()
    monkeypatch.setattr("app.project_bot.messages.queue_voice_input", enqueue)
    incoming = message()
    incoming.audio = SimpleNamespace(duration=duration, file_size=size, file_id="file")
    await audio_message(incoming, *context)
    assert not enqueue.called
    assert "too" in incoming.answer.await_args.args[0]
