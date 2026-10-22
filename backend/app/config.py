from functools import lru_cache
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://taskbot:taskbot@localhost:5432/taskbot"
    redis_url: str = "redis://localhost:6379/0"
    telegram_bot_token: str = ""
    app_origin: str = "http://localhost:8080"
    cookie_secure: bool = False
    whisper_model: str = "small"
    whisper_cache: str = "/models"
    whisper_cpu_threads: int = Field(default=4, ge=1, le=32)
    # Greedy decoding is ~2.7x faster than beam search on CPU for minute-long notes with
    # the same transcript on our fixtures; raise it to trade speed for accuracy.
    whisper_beam_size: int = Field(default=1, ge=1, le=10)
    # Load and warm the model when a transcription worker process starts.
    whisper_preload: bool = False
    log_level: str = "INFO"
    max_voice_bytes: int = 20 * 1024 * 1024
    max_voice_seconds: int = 300
    session_days: int = 7
    app_timezone: str = "Asia/Yerevan"

    @field_validator("app_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @field_validator("app_origin")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        parsed = urlparse(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("APP_ORIGIN must be an http(s) origin without a path")
        return value.rstrip("/")


# Telegram conversation lifetime, shared by the bot's FSM storage and the worker that
# advances a wizard after transcription.
CONVERSATION_TTL_SECONDS = 300


@lru_cache
def settings() -> Settings:
    return Settings()


@lru_cache
def bot_username_key() -> str:
    # The bot's public numeric identity keeps caches separate when credentials change.
    return f"bot:username:{settings().telegram_bot_token.partition(':')[0]}"
