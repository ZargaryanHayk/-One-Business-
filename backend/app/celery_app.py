import logging
import shutil
import tempfile
import time
from pathlib import Path

from celery import Celery
from celery.signals import setup_logging, worker_process_init, worker_ready

from app.config import settings
from app.db import engine
from app.logging import configure_logging

log = logging.getLogger(__name__)
# Far longer than any attempt's 600-second hard limit, so no live job's audio is touched.
STALE_TEMPORARY_SECONDS = 3600

celery = Celery("taskbot", broker=settings().redis_url, include=["app.jobs"])
celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": 900},
    task_routes={
        "app.jobs.transcribe": {"queue": "transcription"},
        "app.jobs.notify": {"queue": "notifications"},
    },
    task_publish_retry_policy={"max_retries": 0},
    worker_hijack_root_logger=False,
)


@setup_logging.connect
def celery_logging(**kwargs):
    configure_logging()


@worker_process_init.connect
def reset_database_pool(**kwargs):
    # Forked children must never reuse a connection inherited from their parent.
    engine.dispose(close=False)


@worker_process_init.connect
def preload_whisper(**kwargs):
    # Load in each forked child (never the parent) so the first voice note is fast.
    if not settings().whisper_preload:
        return
    from app.transcription import warm_up

    try:
        warm_up()
    except Exception as error:
        # Jobs still load the model lazily, so a failed warm-up only costs speed.
        log.warning("whisper_warm_up_failed", extra={"error_type": type(error).__name__})


def sweep_stale_temporary(root: str | None = None, max_age: int = STALE_TEMPORARY_SECONDS) -> int:
    """Remove audio directories left behind when a worker was killed mid-transcription."""
    removed = 0
    cutoff = time.time() - max_age
    for path in Path(root or tempfile.gettempdir()).glob("taskbot-*"):
        try:
            if path.is_dir() and not path.is_symlink() and path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    if removed:
        log.info("stale_temporary_audio_removed", extra={"count": removed})
    return removed


@worker_ready.connect
def sweep_on_start(**kwargs):
    sweep_stale_temporary()
