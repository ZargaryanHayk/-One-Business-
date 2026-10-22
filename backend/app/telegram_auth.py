import asyncio
import logging
import re
import secrets
from contextlib import suppress

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommandScopeChat,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)

from app.auth import (
    LoginRateLimited,
    authenticate,
    consume_telegram_login,
    login_cache,
    peek_telegram_login,
    sign_in_telegram,
    telegram_user,
)
from app.bot_ui import COMMANDS, LOGIN, PUBLIC_COMMANDS, SIGN_IN, login_keyboard, show_main_menu
from app.chat_cleanup import record_message

log = logging.getLogger(__name__)
# Outlives the five-minute login state so a late password message is still removed.
PASSWORD_PROMPT_TTL = 900
SWITCH_TTL = 300
KNOWN_COMMANDS = {f"/{item.command}" for item in COMMANDS + PUBLIC_COMMANDS} | {
    "/cancel",
    "/task",
}
EXPIRED_LINK = (
    "This sign-in link expired or was already used. "
    "Click Open Telegram on the website for a new link, or use /login."
)


class LoginFlow(StatesGroup):
    email = State()
    password = State()


def command_name(value: str) -> str:
    words = value.split(maxsplit=1)
    return words[0].split("@")[0].lower() if words and words[0].startswith("/") else ""


def password_prompt_key(chat_id: int) -> str:
    return f"telegram:password_prompt:{chat_id}"


def mark_password_prompt(chat_id: int) -> None:
    # Only a flag is stored; the password itself never reaches Redis or the FSM.
    login_cache().set(password_prompt_key(chat_id), "1", ex=PASSWORD_PROMPT_TTL)


def clear_password_prompt(chat_id: int) -> bool:
    return bool(login_cache().delete(password_prompt_key(chat_id)))


def save_switch(telegram_id: int, token: str) -> str:
    # Sign-in links stay out of callback data and chat history until Switch is chosen.
    nonce = secrets.token_hex(4)
    login_cache().set(f"telegram:switch:{telegram_id}:{nonce}", token, ex=SWITCH_TTL)
    return nonce


def take_switch(telegram_id: int, nonce: str) -> str | None:
    token = login_cache().getdel(f"telegram:switch:{telegram_id}:{nonce}")
    return token.decode() if token else None


async def begin_login(message: Message, state: FSMContext):
    await state.clear()
    await asyncio.to_thread(clear_password_prompt, message.chat.id)
    await state.set_state(LoginFlow.email)
    await message.answer("Enter your account email:", reply_markup=ReplyKeyboardRemove())


async def locked_message(message: Message):
    await message.answer(
        "Please log in with your account before accessing tasks.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer(
        "Choose Log in to enter your email and password.", reply_markup=login_keyboard()
    )


async def open_link(message: Message, token: str, telegram_id: int):
    target = await asyncio.to_thread(peek_telegram_login, token)
    if not target:
        await message.answer(EXPIRED_LINK)
        return
    current = await asyncio.to_thread(telegram_user, telegram_id)
    if current and current.id != target.id:
        nonce = await asyncio.to_thread(save_switch, telegram_id, token)
        buttons = [
            InlineKeyboardButton(text=label, callback_data=f"authswitch:{nonce}:{choice}")
            for label, choice in (("Switch", "yes"), ("Keep", "no"))
        ]
        await message.answer(
            f"This link signs Telegram into {target.email}. "
            f"You're currently signed in as {current.email}. Switch accounts?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[buttons]),
        )
        return
    user = await asyncio.to_thread(consume_telegram_login, token, telegram_id)
    if user:
        await show_signed_in(message, user)
    else:
        await message.answer(EXPIRED_LINK)


async def confirm_switch(query: CallbackQuery, state: FSMContext):
    _, nonce, choice = (query.data.split(":") + ["", ""])[:3]
    token = await asyncio.to_thread(take_switch, query.from_user.id, nonce)
    if choice != "yes":
        await query.answer()
        current = await asyncio.to_thread(telegram_user, query.from_user.id)
        kept = f"Kept {current.email} signed in. " if current else ""
        with suppress(TelegramAPIError):
            await query.message.edit_text(f"{kept}The sign-in link was not used.")
        return
    if not token:
        await query.answer("This switch request expired.")
        await query.message.answer(EXPIRED_LINK)
        return
    # The link is revalidated and consumed only now, when the person confirms.
    user = await asyncio.to_thread(consume_telegram_login, token, query.from_user.id)
    await query.answer()
    if not user:
        await query.message.answer(EXPIRED_LINK)
        return
    await state.clear()
    with suppress(TelegramAPIError):
        await query.message.edit_text(f"Switched to {user.email}.")
    await show_signed_in(query.message, user)


class TelegramAccess(BaseMiddleware):
    async def __call__(self, handler, event, data):
        callback = isinstance(event, CallbackQuery)
        message = event.message if callback else event
        if (
            not isinstance(message, Message)
            or message.chat.type != "private"
            or not event.from_user
        ):
            if callback:
                # Old, inline, or group buttons still need an answer to stop the spinner.
                with suppress(TelegramAPIError):
                    await event.answer("This button is no longer available. Send /menu.")
            return
        state = data["state"]
        try:
            await asyncio.to_thread(
                record_message, message.chat.id, message.message_id, message.date
            )
            if callback and (event.data or "").startswith("authswitch:"):
                await confirm_switch(event, state)
                return
            value = "" if callback else message.text or ""
            command = command_name(value)
            if not callback:
                parts = value.split(maxsplit=1)
                if command == "/start" and len(parts) == 2 and parts[1].startswith("web_"):
                    await state.clear()
                    # Remove the credential from chat history when Telegram permits it.
                    try:
                        await message.delete()
                    except TelegramAPIError:
                        pass
                    await open_link(message, parts[1][4:], event.from_user.id)
                    return
            owner = await asyncio.to_thread(telegram_user, event.from_user.id)
            if owner:
                data["account"] = owner
                if command == "/cancel":
                    pending = await state.get_state()
                    await state.clear()
                    await message.answer(
                        "Cancelled. Use /projects, /tasks, or /due."
                        if pending
                        else "Nothing to cancel. Use /help to see what you can do."
                    )
                    return
                if command:
                    pending = await state.get_state()
                    if pending and pending.startswith("WorkFlow:"):
                        await state.clear()
                return await handler(event, data)
            if callback:
                await event.answer("" if event.data == "auth:login" else "Log in first")
                if event.data == "auth:login":
                    await begin_login(message, state)
                else:
                    await locked_message(message)
                return
            pending = await state.get_state()
            if pending == LoginFlow.password.state:
                if command == "/cancel":
                    await state.clear()
                    await asyncio.to_thread(clear_password_prompt, message.chat.id)
                    await message.answer("Login cancelled.", reply_markup=login_keyboard())
                    return
                if message.text:
                    # Text starting with "/" is still treated (and deleted) as a password.
                    return await handler(event, data)
                await message.answer("Send your password as text to finish logging in.")
                return
            if command == "/login" or value in {LOGIN, SIGN_IN} or value.startswith("/start login"):
                await begin_login(message, state)
                return
            if (
                message.text
                and command not in KNOWN_COMMANDS
                and await asyncio.to_thread(clear_password_prompt, message.chat.id)
            ):
                # The password step expired, but this is most likely the password.
                await state.clear()
                with suppress(TelegramAPIError):
                    await message.delete()
                await message.answer(
                    "Your login timed out, so I removed that message. Send /login to try again.",
                    reply_markup=login_keyboard(),
                )
                return
            if pending == LoginFlow.email.state:
                if message.text and not command:
                    return await handler(event, data)
                if not command:
                    await message.answer("Send your email as text to finish logging in.")
                    return
                await state.clear()
                if command == "/cancel":
                    await message.answer("Login cancelled.", reply_markup=login_keyboard())
                    return
            if command == "/cancel":
                await message.answer("Nothing to cancel.", reply_markup=login_keyboard())
                return
            await locked_message(message)
        except Exception:
            # Authentication storage failing must never permit access to task handlers.
            log.exception("telegram_auth_unavailable")
            await message.answer(
                "Login is temporarily unavailable. Please try /login again.",
                reply_markup=login_keyboard(),
            )


async def collect_email(message: Message, state: FSMContext):
    email = (message.text or "").strip().casefold()
    if len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        await message.answer("Please enter a valid email address, or /cancel to stop.")
        return
    await state.update_data(email=email)
    await state.set_state(LoginFlow.password)
    await asyncio.to_thread(mark_password_prompt, message.chat.id)
    await message.answer("Now enter your password. I will try to remove the password message.")


async def collect_password(message: Message, state: FSMContext):
    password = message.text or ""
    # Password messages are credentials, never task input or stored FSM data.
    try:
        await message.delete()
    except TelegramAPIError:
        pass
    data = await state.get_data()
    await state.clear()
    try:
        await asyncio.to_thread(clear_password_prompt, message.chat.id)
        if not data.get("email") or not 1 <= len(password) <= 256:
            user = None
        else:
            user = await asyncio.to_thread(
                authenticate,
                data["email"],
                password,
                f"telegram:{message.from_user.id}",
            )
        if not user:
            await message.answer(
                "Email or password is incorrect. Please try logging in again.",
                reply_markup=login_keyboard(),
            )
            return
        await asyncio.to_thread(sign_in_telegram, message.from_user.id, user)
        await show_signed_in(message, user)
    except LoginRateLimited:
        await message.answer(
            "Too many login attempts. Wait a minute, then try /login again.",
            reply_markup=login_keyboard(),
        )
    except Exception:
        log.exception("telegram_login_failed")
        await message.answer(
            "Login is temporarily unavailable. Please try /login again.",
            reply_markup=login_keyboard(),
        )


async def show_signed_in(message: Message, user):
    await asyncio.to_thread(clear_password_prompt, message.chat.id)
    await message.bot.set_my_commands(COMMANDS, scope=BotCommandScopeChat(chat_id=message.chat.id))
    await show_main_menu(
        message,
        f"Logged in as {user.name} ({user.email}) · {user.role.replace('_', ' ')}.\n\n"
        "Send a text or voice message to create a task, or use the buttons below.",
    )
