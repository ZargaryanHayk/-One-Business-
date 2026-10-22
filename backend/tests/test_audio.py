import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from app.transcription import PermanentVoiceError, download_voice, transcribe_audio


def wav(path: Path, seconds: int):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * 16000 * seconds)


def test_corrupt_recording(tmp_path):
    path = tmp_path / "bad.ogg"
    path.write_bytes(b"not an audio file")
    with pytest.raises(PermanentVoiceError, match="decode"):
        transcribe_audio(path)


def test_silence_and_actual_duration_limit(tmp_path):
    path = tmp_path / "silence.wav"
    wav(path, 1)
    with pytest.raises(PermanentVoiceError, match="No speech"):
        transcribe_audio(path)
    wav(path, 301)
    with pytest.raises(PermanentVoiceError, match="5 minute"):
        transcribe_audio(path)


def test_empty_transcript_is_rejected(monkeypatch):
    model = SimpleNamespace(transcribe=Mock(return_value=(iter([]), None)))
    monkeypatch.setattr("app.transcription.whisper_model", lambda: model)
    with pytest.raises(PermanentVoiceError, match="No speech"):
        transcribe_audio(Path(__file__).parent / "fixtures" / "task.wav")


def test_download_checks_streamed_size(monkeypatch, tmp_path):
    from app.config import settings

    config = settings()
    monkeypatch.setattr(config, "max_voice_bytes", 4)

    def response(request):
        if request.url.path.endswith("getFile"):
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "voice/x.oga"}})
        return httpx.Response(200, content=b"too much data")

    original_client = httpx.Client
    monkeypatch.setattr(
        "app.transcription.httpx.Client",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(response),
            **kwargs,
        ),
    )
    with pytest.raises(PermanentVoiceError, match="20 MB"):
        download_voice("file", tmp_path / "download.ogg")
