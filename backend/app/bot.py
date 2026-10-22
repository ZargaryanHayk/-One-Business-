import asyncio
import logging
import uuid
from contextlib import suppress

import redis.asyncio as aioredis
from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import (
    BotCommandScopeChat,
    CallbackQuery,
    LinkPreviewOptions,
    MenuButtonCommands,
    Message,
    ReplyKeyboardRemove,
)

from app.bot_ui import (
    COMMANDS,
    HELP,
    LOGIN,
    LOGOUT,
    MENU,
    PUBLIC_COMMANDS,
    TASKS,
    login_keyboard,
    logout_keyboard,
    main_keyboard,
    show_main_menu,
    tasks_keyboard,
    tasks_text,
)
from app.chat_cleanup import TrackOutgoingMessages
from app.config import CONVERSATION_TTL_SECONDS, bot_username_key, settings
from app.domain import (
    create_text_task,
    dashboard_token,
    logout_telegram_user,
    retry_voice,
    telegram_tasks,
)
from app.logging import configure_logging
from app.project_bot import register as register_projects
from app.project_bot.common import WorkFlow
from app.project_bot.messages import collect_voice_answer
from app.telegram_auth import LoginFlow, TelegramAccess, collect_email, collect_password
from app.workspace import WorkspaceError

log = logging.getLogger(__name__)
router = Router()
router.message.filter(F.chat.type == ChatType.PRIVATE)
router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)
router.message.outer_middleware(TelegramAccess())
router.callback_query.outer_middleware(TelegramAccess())
router.message.register(collect_email, LoginFlow.email, F.text)
router.message.register(collect_password, LoginFlow.password, F.text)


@router.message(Command("start", "help", "menu"))
@router.message(F.text.in_({HELP, MENU}))
async def help_command(
    message: Message,
    command: CommandObject | None = None,
    state: FSMContext | None = None,
):
    if state:
        await state.clear()
    if command and command.command == "start" and command.args == "login":
        await dashboard_command(message, state)
        return
    is_menu = (command and command.command in {"start", "menu"}) or message.text == MENU
    if is_menu:
        await message.bot.set_my_commands(
            COMMANDS, scope=BotCommandScopeChat(chat_id=message.chat.id)
        )
    await show_main_menu(
        message,
        "Send a text or voice message to create a task.\n"
        "Use ＋ Add task or /newtask to choose a personal task or project.\n"
        "Voice notes can be up to 5 minutes and 20 MB.\n"
        "Use /projects for project tasks, assignments, deadlines, and comments.\n"
        "Use /due for incomplete tasks due within four days (including overdue).\n"
        "\n📋 My tasks — browse your tasks.\n"
        "＋ Add task — choose Personal task or a project, enter a title and description, "
        "then choose a deadline.\n"
        "/dashboard — get a private website sign-in link.\n"
        "🚪 Log out — sign out of your website sessions.\n\n"
        "You are logged in. Your tasks are shared with your website account.\n"
        "Sign-in links work once for 5 minutes.",
    )


@router.message(Command("dashboard", "login"))
@router.message(F.text == LOGIN)
async def dashboard_command(message: Message, state: FSMContext | None = None):
    if state:
        await state.clear()
    try:
        url = await asyncio.to_thread(
            dashboard_token,
            message.from_user.id,
            message.from_user.full_name,
        )
        await message.answer(
            f"Your private dashboard (valid for 5 minutes):\n{url}\n"
            "Open it on the computer running this app. Keep this link private.",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
            reply_markup=main_keyboard(),
        )
    except Exception:
        log.exception("dashboard_link_failed")
        await message.answer("Could not create a sign-in link. Please try /dashboard again.")


@router.message(Command("tasks"))
@router.message(F.text == TASKS)
async def tasks_command(message: Message, state: FSMContext | None = None):
    if state:
        await state.clear()
    try:
        tasks, has_next = await asyncio.to_thread(telegram_tasks, message.from_user.id)
        await message.answer(
            tasks_text(tasks, 0), reply_markup=tasks_keyboard(0, has_next, tasks), parse_mode="HTML"
        )
    except Exception:
        log.exception("task_list_failed")
        await message.answer("Could not load your tasks. Please try /tasks again.")


@router.message(Command("logout"))
@router.message(F.text == LOGOUT)
async def logout_command(message: Message, state: FSMContext | None = None):
    if state:
        await state.clear()
    await message.answer(
        "Log out of all your website sessions or log out of Telegram only?\n\n"
        "• Log out of Telegram only — signs you out of this bot "
        "while keeping your website sessions active.\n"
        "• Log out of all website sessions — revokes this Telegram login "
        "and all your website sessions.\n\n"
        "Recent chat messages will be cleared. Your tasks will stay saved. "
        "You will need to log in again to access the bot.",
        reply_markup=logout_keyboard(),
    )


@router.callback_query(F.data.in_({"menu:tasks", "menu:help", "menu:logout_prompt"}))
async def menu_callback(query: CallbackQuery, state: FSMContext | None = None):
    await query.answer()
    if state:
        await state.clear()
    if query.data == "menu:help":
        await help_command(query.message, state=state)
        return
    if query.data == "menu:logout_prompt":
        await logout_command(query.message, state=state)
        return
    try:
        tasks, has_next = await asyncio.to_thread(telegram_tasks, query.from_user.id)
        await query.message.answer(
            tasks_text(tasks, 0), reply_markup=tasks_keyboard(0, has_next, tasks), parse_mode="HTML"
        )
    except Exception:
        log.exception("task_list_failed")
        await query.message.answer("Could not load your tasks. Please try /tasks again.")


@router.callback_query(F.data == "menu:main")
async def main_menu_callback(query: CallbackQuery, state: FSMContext | None = None):
    await query.answer()
    if state:
        await state.clear()
    await show_main_menu(query.message)


@router.callback_query(F.data == "auth:login")
async def already_logged_in_callback(query: CallbackQuery, state: FSMContext | None = None):
    await query.answer("You are already logged in")
    if state:
        await state.clear()
    await show_main_menu(query.message)


@router.callback_query(F.data.startswith("tasks:"))
async def tasks_callback(query: CallbackQuery, state: FSMContext | None = None):
    if state:
        # Browsing the list leaves any project/task conversation.
        await state.clear()
    notice = None
    try:
        page = int(query.data.removeprefix("tasks:"))
        tasks, has_next = await asyncio.to_thread(telegram_tasks, query.from_user.id, page)
        # message.text is plain text while content is HTML, so comparing them never matched;
        # Telegram itself reports an identical text and keyboard as "not modified".
        await query.message.edit_text(
            tasks_text(tasks, page),
            reply_markup=tasks_keyboard(page, has_next, tasks),
            parse_mode="HTML",
        )
    except ValueError:
        await query.message.answer("Invalid page. Use /tasks to open your task list.")
    except TelegramBadRequest as error:
        if "message is not modified" in str(error).lower():
            notice = "Already up to date"
        else:
            log.exception("task_list_edit_failed")
            await query.message.answer("Please use /tasks to refresh your task list.")
    except Exception:
        log.exception("task_list_failed")
        await query.message.answer("Could not load your tasks. Please try /tasks again.")
    await query.answer(notice)


@router.callback_query(F.data == "menu:login")
async def login_callback(query: CallbackQuery):
    await query.answer()
    try:
        url = await asyncio.to_thread(
            dashboard_token,
            query.from_user.id,
            query.from_user.full_name,
        )
        await query.message.answer(
            f"Your private dashboard (valid for 5 minutes):\n{url}\n"
            "Open it on the computer running this app. Keep this link private.",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
            reply_markup=main_keyboard(),
        )
    except Exception:
        log.exception("dashboard_link_failed")
        await query.message.answer("Could not create a sign-in link. Please try /login again.")


@router.callback_query(F.data.in_({"menu:logout", "menu:logout_telegram"}))
async def logout_callback(query: CallbackQuery, state=None):
    await query.answer()
    all_devices = query.data == "menu:logout"
    try:
        await query.message.answer("Logging out…", reply_markup=ReplyKeyboardRemove())
        if all_devices:
            count = await asyncio.to_thread(logout_telegram_user, query.from_user.id)
            confirmation = (
                f"Logged out of Telegram and {count} website session(s). "
                "Your tasks are saved. Log in to continue."
            )
        else:
            await asyncio.to_thread(logout_telegram_user, query.from_user.id, False)
            confirmation = (
                "Logged out of Telegram only. Your website sessions remain active. "
                "Your tasks are saved. Log in to continue."
            )
        if state is not None:
            await state.clear()
        await query.message.answer(
            confirmation,
            reply_markup=login_keyboard(),
        )
        await query.bot.set_my_commands(
            PUBLIC_COMMANDS, scope=BotCommandScopeChat(chat_id=query.message.chat.id)
        )
    except Exception:
        log.exception("dashboard_logout_failed")
        await query.message.answer("Could not sign out. Please try /logout again.")


@router.callback_query(F.data == "menu:cancel")
async def cancel_callback(query: CallbackQuery):
    await query.answer()
    await query.message.edit_text("Cancelled. Your website sessions are unchanged.")


register_projects(router)


@router.message(F.voice)
async def voice_message(message: Message, account, state: FSMContext):
    voice = message.voice
    if (voice.duration or 0) > settings().max_voice_seconds:
        await message.answer("This voice note is too long. Please send up to 5 minutes.")
        return
    if voice.file_size and voice.file_size > settings().max_voice_bytes:
        await message.answer("This voice note is too large. Please send up to 20 MB.")
        return
    try:
        await state.clear()
        await state.set_state(WorkFlow.quick_voice)
        await collect_voice_answer(message, account, state, WorkFlow.quick_voice.state, {})
    except Exception:
        log.exception("voice_enqueue_failed")
        await message.answer("Could not save this voice note. Please send it again.")
        return


@router.message(F.audio)
async def audio_message(message: Message, account, state: FSMContext):
    audio = message.audio
    if (audio.duration or 0) > settings().max_voice_seconds:
        await message.answer("This audio file is too long. Please send up to 5 minutes.")
        return
    if audio.file_size and audio.file_size > settings().max_voice_bytes:
        await message.answer("This audio file is too large. Please send up to 20 MB.")
        return
    # Treat uploaded audio files the same as voice notes — transcribe and create a task.
    try:
        await state.clear()
        await state.set_state(WorkFlow.quick_voice)
        await collect_voice_answer(message, account, state, WorkFlow.quick_voice.state, {})
    except Exception:
        log.exception("audio_enqueue_failed")
        await message.answer("Could not save this audio file. Please send it again.")
        return


@router.message(F.video_note)
async def video_note_message(message: Message):
    await message.answer(
        "Round video notes are not supported. Please send a voice note or a text message."
    )


@router.callback_query(F.data.startswith("retry:"))
async def retry_callback(query: CallbackQuery):
    try:
        job_id = uuid.UUID(query.data.removeprefix("retry:"))
        accepted = await asyncio.to_thread(retry_voice, job_id, query.from_user.id)
        await query.answer("Queued again." if accepted else "This job cannot be retried.")
    except (ValueError, AttributeError):
        await query.answer("Invalid retry request.")
    except WorkspaceError as error:
        await query.answer(error.message, show_alert=True)
    except Exception:
        log.exception("voice_retry_failed")
        await query.answer("Retry unavailable. Please try again shortly.")


@router.message(F.text)
async def text_message(message: Message):
    if message.text.startswith("/"):
        await message.answer("Unknown command. Use /help for instructions.")
        return
    if not message.text.strip():
        await message.answer("Please send some task text.")
        return
    try:
        await asyncio.to_thread(
            create_text_task,
            message.from_user.id,
            message.from_user.full_name,
            message.chat.id,
            message.message_id,
            message.text,
        )
    except Exception:
        log.exception("text_creation_failed")
        await message.answer("Could not save this task. Please send it again.")


@router.message()
async def unsupported_message(message: Message):
    await message.answer("Please send a text message or a voice note. Use /help for instructions.")


async def heartbeat(username: str | None = None):
    cache = aioredis.from_url(settings().redis_url, socket_connect_timeout=5, socket_timeout=5)
    try:
        while True:
            try:
                await cache.set("heartbeat:bot", "alive", ex=30)
                if username:
                    await cache.set(bot_username_key(), username, ex=60)
            except aioredis.RedisError:
                log.warning("bot_heartbeat_unavailable")
            await asyncio.sleep(10)
    finally:
        await cache.aclose()


async def main():
    configure_logging()
    if not settings().telegram_bot_token:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN before starting the bot")
    bot = Bot(settings().telegram_bot_token)
    bot.session.middleware(TrackOutgoingMessages())
    # Register supported commands so Telegram's Menu button is useful immediately.
    await bot.set_my_commands(PUBLIC_COMMANDS)
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    identity = await bot.me()
    storage = RedisStorage.from_url(
        settings().redis_url,
        state_ttl=CONVERSATION_TTL_SECONDS,
        data_ttl=CONVERSATION_TTL_SECONDS,
    )
    dispatcher = Dispatcher(storage=storage)
    dispatcher.include_router(router)
    # Process each update durably before polling advances its offset.
    heartbeat_task = asyncio.create_task(heartbeat(identity.username))
    try:
        await dispatcher.start_polling(bot, handle_as_tasks=False)
    finally:
        heartbeat_task.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat_task


if __name__ == "__main__":
    asyncio.run(main())
