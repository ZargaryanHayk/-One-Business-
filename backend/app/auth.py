import hashlib
import re
from datetime import timedelta
from functools import lru_cache

import redis
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import (
    LoginSession,
    Task,
    TelegramLoginToken,
    TelegramSession,
    User,
    VoiceJob,
    now,
)

hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
DUMMY_HASH = (
    "$argon2id$v=19$m=19456,t=2,p=1$2hCq8WiiDKrFrznjUe7bBg$"
    "p85AalIkHEbPrqN3aIwjUAMKCVJv7QS2pCwXfC5CA/o"
)
LIMIT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], 60) end
return count
"""


class LoginRateLimited(Exception):
    pass


@lru_cache
def login_cache():
    return redis.Redis.from_url(settings().redis_url, socket_timeout=3, socket_connect_timeout=3)


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def authenticate(email: str, password: str, source: str) -> User | None:
    email = normalize_email(email)
    cache = login_cache()
    for identity, maximum in ((f"source:{source}", 30), (f"email:{email}", 10)):
        key = hashlib.sha256(identity.encode()).hexdigest()
        if cache.eval(LIMIT_SCRIPT, 1, f"auth:limit:{key}") > maximum:
            raise LoginRateLimited
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        encoded = user.password_hash if user and user.password_hash else DUMMY_HASH
        try:
            valid = hasher.verify(encoded, password)
        except (VerificationError, InvalidHashError):
            valid = False
        return user if valid and user and user.password_hash else None


def telegram_user(telegram_id: int) -> User | None:
    with SessionLocal() as db:
        return db.scalar(
            select(User)
            .join(TelegramSession)
            .where(
                TelegramSession.telegram_id == telegram_id,
                TelegramSession.expires_at > now(),
                User.password_hash.is_not(None),
            )
        )


def sign_in_telegram(telegram_id: int, user: User) -> None:
    with SessionLocal.begin() as db:
        db.execute(select(User.id).where(User.id == user.id).with_for_update())
        _sign_in_telegram(db, telegram_id, user)


def _sign_in_telegram(
    db: Session, telegram_id: int, user: User, *, migrate_legacy: bool = True
) -> None:
    # Only a password login proves the chat owner chose this account; a forwarded
    # sign-in link must never move the chat's legacy Telegram-only data elsewhere.
    legacy = migrate_legacy and db.scalar(
        select(User.id).where(
            User.telegram_id == telegram_id,
            User.password_hash.is_(None),
            User.id != user.id,
        )
    )
    if legacy:
        from app.domain import task_event

        tasks = list(db.scalars(select(Task).where(Task.user_id == legacy)))
        for task in tasks:
            task.user_id = user.id
            if task.assignee_id == legacy:
                task.assignee_id = user.id
            task_event(db, task)
        db.execute(update(VoiceJob).where(VoiceJob.user_id == legacy).values(user_id=user.id))
    db.execute(
        insert(TelegramSession)
        .values(
            telegram_id=telegram_id,
            user_id=user.id,
            expires_at=now() + timedelta(days=settings().session_days),
        )
        .on_conflict_do_update(
            index_elements=[TelegramSession.telegram_id],
            set_={
                "user_id": user.id,
                "expires_at": now() + timedelta(days=settings().session_days),
            },
        )
    )


def consume_telegram_login(token: str, telegram_id: int) -> User | None:
    """Consume a link and establish the bot session atomically with logout/revocation."""
    from app.domain import digest

    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        return None
    with SessionLocal.begin() as db:
        token_hash = digest(token)
        owner_id = db.scalar(
            select(LoginSession.user_id)
            .join(TelegramLoginToken, TelegramLoginToken.session_hash == LoginSession.token_hash)
            .where(TelegramLoginToken.token_hash == token_hash)
        )
        if owner_id is None:
            return None
        user = db.scalar(select(User).where(User.id == owner_id).with_for_update())
        if user is None or user.password_hash is None:
            return None
        session_hash = db.scalar(
            select(LoginSession.token_hash)
            .join(TelegramLoginToken, TelegramLoginToken.session_hash == LoginSession.token_hash)
            .where(
                TelegramLoginToken.token_hash == token_hash,
                LoginSession.expires_at > now(),
                LoginSession.user_id == owner_id,
            )
        )
        if session_hash is None:
            return None
        consumed = db.scalar(
            delete(TelegramLoginToken)
            .where(
                TelegramLoginToken.token_hash == token_hash,
                TelegramLoginToken.expires_at > now(),
            )
            .returning(TelegramLoginToken.token_hash)
        )
        if consumed is None:
            return None
        _sign_in_telegram(db, telegram_id, user, migrate_legacy=False)
        return user


def peek_telegram_login(token: str) -> User | None:
    """Return the account a valid, unexpired link would sign in, without consuming it."""
    from app.domain import digest

    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        return None
    with SessionLocal() as db:
        return db.scalar(
            select(User)
            .join(LoginSession, LoginSession.user_id == User.id)
            .join(TelegramLoginToken, TelegramLoginToken.session_hash == LoginSession.token_hash)
            .where(
                TelegramLoginToken.token_hash == digest(token),
                TelegramLoginToken.expires_at > now(),
                LoginSession.expires_at > now(),
                User.password_hash.is_not(None),
            )
        )
