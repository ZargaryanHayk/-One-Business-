import functools
import logging
import time
from pathlib import Path

import httpx

from app.config import settings

log = logging.getLogger(__name__)


class PermanentVoiceError(Exception):
    """A safe, user-facing rejection of an invalid recording."""


class TelegramRetryAfter(Exception):
    """Telegram rate limiting (HTTP 429); retry no sooner than ``seconds``."""

    def __init__(self, seconds: int):
        super().__init__(f"Retry after {seconds} seconds")
        self.seconds = seconds


def check_rate_limit(response: httpx.Response) -> None:
    if response.status_code != 429:
        return
    try:
        response.read()
        seconds = int(response.json().get("parameters", {}).get("retry_after", 0))
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        seconds = 0
    raise TelegramRetryAfter(max(1, min(seconds, 3600)))


def retry_countdown(error: Exception, backoff: int) -> int:
    """Exponential backoff, but never sooner than Telegram's requested delay."""
    return max(backoff, error.seconds) if isinstance(error, TelegramRetryAfter) else backoff


UNAVAILABLE = "Telegram could not retrieve this voice note. Send it again."


def download_voice(file_id: str, destination: Path) -> None:
    config = settings()
    with httpx.Client(timeout=httpx.Timeout(60, connect=15)) as client:
        metadata = client.get(
            f"https://api.telegram.org/bot{config.telegram_bot_token}/getFile",
            params={"file_id": file_id},
        )
        if metadata.status_code in {400, 404}:
            raise PermanentVoiceError(UNAVAILABLE)
        check_rate_limit(metadata)
        metadata.raise_for_status()
        result = metadata.json()
        if not isinstance(result, dict) or not result.get("ok"):
            raise PermanentVoiceError(UNAVAILABLE)
        file = result.get("result")
        if not isinstance(file, dict):
            raise PermanentVoiceError(UNAVAILABLE)
        if (file.get("file_size") or 0) > config.max_voice_bytes:
            raise PermanentVoiceError("Voice note exceeds the 20 MB limit.")
        path = file.get("file_path")
        if not path:
            # Telegram omits the path for files it can no longer serve (or that exceed 20 MB).
            raise PermanentVoiceError(UNAVAILABLE)
        total = 0
        with client.stream(
            "GET", f"https://api.telegram.org/file/bot{config.telegram_bot_token}/{path}"
        ) as response:
            if response.status_code in {400, 404}:
                raise PermanentVoiceError(UNAVAILABLE)
            check_rate_limit(response)
            response.raise_for_status()
            with destination.open("wb") as output:
                for chunk in response.iter_bytes(65536):
                    total += len(chunk)
                    if total > config.max_voice_bytes:
                        raise PermanentVoiceError("Voice note exceeds the 20 MB limit.")
                    output.write(chunk)


@functools.lru_cache(maxsize=1)
def whisper_model():
    from faster_whisper import WhisperModel

    config = settings()
    return WhisperModel(
        config.whisper_model,
        device="cpu",
        compute_type="int8",
        cpu_threads=config.whisper_cpu_threads,
        download_root=config.whisper_cache,
    )


def warm_up() -> None:
    """Load the model and run one tiny inference so the first voice note isn't slowed."""
    import numpy as np

    started = time.perf_counter()
    segments, _ = whisper_model().transcribe(
        np.zeros(16000, dtype=np.float32), beam_size=1, vad_filter=False
    )
    list(segments)  # Segments are lazy; consuming them runs the encoder and decoder.
    log.info("whisper_warmed", extra={"duration_ms": round((time.perf_counter() - started) * 1000)})


def transcribe_audio(path: Path) -> str:
    import av
    import numpy as np
    from av.error import FFmpegError

    chunks = []
    samples = 0
    try:
        with av.open(str(path)) as container:
            resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
            for frame in container.decode(audio=0):
                frame.pts = None
                for converted in resampler.resample(frame):
                    chunk = converted.to_ndarray().flatten()
                    samples += len(chunk)
                    if samples > settings().max_voice_seconds * 16000:
                        raise PermanentVoiceError("Voice note exceeds the 5 minute limit.")
                    chunks.append(chunk)
            for converted in resampler.resample(None):
                chunk = converted.to_ndarray().flatten()
                samples += len(chunk)
                if samples > settings().max_voice_seconds * 16000:
                    raise PermanentVoiceError("Voice note exceeds the 5 minute limit.")
                chunks.append(chunk)
    except (FFmpegError, ValueError, EOFError, IndexError):
        raise PermanentVoiceError(
            "Could not decode this voice note. Please record it again."
        ) from None
    if not samples:
        raise PermanentVoiceError("No speech detected. Please record your task again.")
    audio = np.concatenate(chunks).astype(np.float32) / 32768.0
    if not np.any(audio):
        raise PermanentVoiceError("No speech detected. Please record your task again.")
    segments, _ = whisper_model().transcribe(
        audio, vad_filter=True, beam_size=settings().whisper_beam_size
    )
    transcript = " ".join(segment.text.strip() for segment in segments).strip()
    if not transcript:
        raise PermanentVoiceError("No speech detected. Please record your task again.")
    return transcript
