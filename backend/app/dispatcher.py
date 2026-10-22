import json
import logging
import signal
import time
import uuid
from datetime import timedelta

import kombu.exceptions
import redis
from sqlalchemy import and_, delete, or_, select

from app.celery_app import celery
from app.config import settings
from app.db import SessionLocal
from app.domain import outbox
from app.logging import configure_logging
from app.models import (
    ChatCleanup,
    LoginSession,
    LoginToken,
    Outbox,
    TelegramLoginToken,
    TelegramMessage,
    VoiceJob,
    now,
)

log = logging.getLogger(__name__)
stopping = False
# Just above the transcription task's 600-second hard limit: a job still "processing" after
# this was killed (time limit, OOM, container stop) and its Redis message may be acknowledged.
STALE_PROCESSING = timedelta(minutes=11)
# Queued jobs may legitimately wait behind a backlog on the single worker; only repair
# messages that were genuinely lost (for example a flushed broker).
STALE_QUEUED = timedelta(minutes=60)
OUTBOX_MAX_ATTEMPTS = 5
TASKS = {
    "transcribe": ("app.jobs.transcribe", "transcription"),
    "notification": ("app.jobs.notify", "notifications"),
    "chat_action": ("app.jobs.chat_action", "notifications"),
    "reminder": ("app.jobs.remind", "notifications"),
    "assignment": ("app.jobs.assignment_notice", "notifications"),
    "chat_cleanup": ("app.jobs.cleanup_chat", "notifications"),
}
# Broker/network outages stop the batch without counting against any row.
OUTAGES = (redis.RedisError, kombu.exceptions.OperationalError, OSError)


def recover_stale_jobs() -> int:
    """Repair jobs stranded by a database outage, lost broker data, or worker failure."""
    recovered = 0
    with SessionLocal.begin() as db:
        jobs = db.scalars(
            select(VoiceJob)
            .where(
                or_(
                    and_(
                        VoiceJob.status == "processing",
                        VoiceJob.updated_at < now() - STALE_PROCESSING,
                    ),
                    and_(
                        VoiceJob.status == "queued",
                        VoiceJob.updated_at < now() - STALE_QUEUED,
                    ),
                )
            )
            .with_for_update(skip_locked=True)
            .limit(25)
        )
        for job in jobs:
            job.status = "queued"
            job.updated_at = now()
            outbox(
                db,
                "transcribe",
                f"recover:{job.id}:{job.generation}:{uuid.uuid4()}",
                job_id=str(job.id),
                generation=job.generation,
            )
            recovered += 1
        for cleanup in db.scalars(
            select(ChatCleanup)
            .where(
                ChatCleanup.completed_at.is_(None),
                ChatCleanup.updated_at < now() - timedelta(minutes=20),
            )
            .with_for_update(skip_locked=True)
            .limit(25)
        ):
            outbox(
                db,
                "chat_cleanup",
                f"recover-cleanup:{cleanup.id}:{uuid.uuid4()}",
                cleanup_id=str(cleanup.id),
            )
            cleanup.updated_at = now()
            recovered += 1
    return recovered


def publish(client: redis.Redis, row: Outbox) -> None:
    if row.kind == "event":
        client.publish(
            f"events:{row.payload['user_id']}", json.dumps({"task_id": row.payload["task_id"]})
        )
        return
    task_name, queue = TASKS[row.kind]
    celery.send_task(task_name, kwargs=row.payload, queue=queue, task_id=str(row.id), retry=False)


def dispatch_once(client: redis.Redis) -> int:
    """Publish pending rows; one malformed row cannot block or re-send the others."""
    delivered = 0
    outage = None
    with SessionLocal.begin() as db:
        rows = list(
            db.scalars(
                select(Outbox)
                .where(Outbox.delivered_at.is_(None), Outbox.attempts < OUTBOX_MAX_ATTEMPTS)
                .order_by(Outbox.created_at)
                .limit(25)
                .with_for_update(skip_locked=True)
            )
        )
        for row in rows:
            try:
                with db.begin_nested():
                    publish(client, row)
                    row.delivered_at = now()
                    db.flush()
                delivered += 1
            except OUTAGES as error:
                # Keep the rows already published in this batch marked as delivered.
                outage = error
                break
            except Exception as error:
                row.attempts += 1
                row.last_error = type(error).__name__[:100]
                quarantined = row.attempts >= OUTBOX_MAX_ATTEMPTS
                (log.error if quarantined else log.warning)(
                    "outbox_row_quarantined" if quarantined else "outbox_row_failed",
                    extra={
                        "outbox_id": str(row.id),
                        "kind": row.kind,
                        "attempts": row.attempts,
                        "error_type": row.last_error,
                    },
                )
    if outage is not None:
        raise outage
    return delivered


def run_periodic(name: str, operation) -> None:
    """Periodic maintenance must never stop outbox delivery or skip its own schedule."""
    try:
        operation()
    except Exception as error:
        log.warning(f"{name}_failed", extra={"error_type": type(error).__name__})


def cleanup_expired() -> None:
    with SessionLocal.begin() as db:
        db.execute(delete(LoginToken).where(LoginToken.expires_at < now()))
        db.execute(delete(TelegramLoginToken).where(TelegramLoginToken.expires_at < now()))
        db.execute(delete(LoginSession).where(LoginSession.expires_at < now()))
        db.execute(
            delete(TelegramMessage).where(TelegramMessage.sent_at < now() - timedelta(hours=48))
        )
        db.execute(delete(ChatCleanup).where(ChatCleanup.completed_at < now() - timedelta(days=7)))
        db.execute(delete(Outbox).where(Outbox.delivered_at < now() - timedelta(days=7)))


def stop(signum, frame):
    global stopping
    stopping = True


def main():
    configure_logging()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    client = redis.Redis.from_url(settings().redis_url, socket_timeout=5, socket_connect_timeout=5)
    last_cleanup = 0
    last_recovery = 0
    last_reminders = 0
    backoff = 1
    while not stopping:
        try:
            client.set("heartbeat:dispatcher", str(time.time()), ex=30)
            count = dispatch_once(client)
            if time.monotonic() - last_reminders > 60:
                from app.reminders import schedule_reminders

                last_reminders = time.monotonic()
                run_periodic("reminder_schedule", schedule_reminders)
            if time.monotonic() - last_recovery > 60:
                last_recovery = time.monotonic()
                run_periodic("stale_job_recovery", recover_stale_jobs)
            if time.monotonic() - last_cleanup > 3600:
                last_cleanup = time.monotonic()
                run_periodic("expired_cleanup", cleanup_expired)
            backoff = 1
            if not count:
                time.sleep(0.25)
        except Exception as error:
            log.warning("outbox_dispatch_failed", extra={"error_type": type(error).__name__})
            time.sleep(backoff)
            backoff = min(backoff * 2, 10)
    client.close()


if __name__ == "__main__":
    main()
