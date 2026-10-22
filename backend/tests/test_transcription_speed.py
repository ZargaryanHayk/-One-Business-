from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app import celery_app, transcription
from app.config import settings


def test_transcription_uses_configured_beam_size(monkeypatch):
    model = Mock()
    model.transcribe.return_value = ([SimpleNamespace(text=" Buy milk ")], None)
    monkeypatch.setattr(transcription, "whisper_model", lambda: model)
    monkeypatch.setattr(settings(), "whisper_beam_size", 3)
    fixture = Path(__file__).parent / "fixtures" / "task.wav"
    assert transcription.transcribe_audio(fixture) == "Buy milk"
    assert model.transcribe.call_args.kwargs["beam_size"] == 3
    assert model.transcribe.call_args.kwargs["vad_filter"] is True


def test_greedy_decoding_is_the_default():
    assert type(settings()).model_fields["whisper_beam_size"].default == 1


@pytest.mark.parametrize("enabled", [True, False])
def test_worker_preloads_model_only_when_enabled(monkeypatch, enabled):
    warm = Mock()
    monkeypatch.setattr(transcription, "warm_up", warm)
    monkeypatch.setattr(settings(), "whisper_preload", enabled)
    celery_app.preload_whisper()
    assert warm.called is enabled


def test_failed_warm_up_does_not_stop_the_worker(monkeypatch):
    monkeypatch.setattr(transcription, "warm_up", Mock(side_effect=RuntimeError("no model")))
    monkeypatch.setattr(settings(), "whisper_preload", True)
    celery_app.preload_whisper()  # Must not raise; jobs fall back to lazy loading.


def test_warm_up_consumes_lazy_segments(monkeypatch):
    consumed = []

    def segments():
        consumed.append(True)
        yield SimpleNamespace(text="")

    model = Mock()
    model.transcribe.return_value = (segments(), None)
    monkeypatch.setattr(transcription, "whisper_model", lambda: model)
    transcription.warm_up()
    assert consumed == [True]
