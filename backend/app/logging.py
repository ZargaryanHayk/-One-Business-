import json
import logging
from datetime import UTC, datetime

from app.config import settings


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key in (
            "job_id",
            "attempt",
            "duration_seconds",
            "error_type",
            "outbox_id",
            "rejection_reason",
        ):
            if hasattr(record, key):
                entry[key] = getattr(record, key)
        # Do not serialize exception text: Telegram HTTP URLs include the bot token.
        if record.exc_info:
            entry["error_type"] = record.exc_info[0].__name__
        return json.dumps(entry)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=settings().log_level, handlers=[handler], force=True)
    for name in ("httpx", "httpcore", "aiogram.event", "celery.app.trace"):
        logging.getLogger(name).setLevel(logging.WARNING)
