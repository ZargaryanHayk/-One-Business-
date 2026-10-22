"""Commands and typed or recorded replies, dispatched by the current conversation state."""

import asyncio
import secrets
import uuid

from app import workspace as ws
from app.bot_ui import ADD_TASK, MENU, show_main_menu
from app.domain import queue_voice, queue_voice_edit, queue_voice_input, queue_voice_title
from app.project_bot.common import (
    CANCELLED,
    DUE,
    TITLE_PROMPT,
    VOICE_LIMIT,
    WorkFlow,
    cancel_keyboard,
    keyboard,
    parse_due,
    voice_too_large,
)
from app.project_bot.views import (
    due_tasks,
    new_task_prompt,
    show_list,
    show_project,
    show_projects,
    show_task,
)
from app.project_bot.wizard import (
    apply_deadline,
    ask_project_name,
    choose_assignee,
    finish_description,
)
from app.voice_input import INPUT_STATES, VOICE_ANSWER, review_messages, spoken_email


async def project_command(message, account, state, command=None):
    await state.clear()
    name = command.command if command else ""
    if name == "newtask" or message.text == ADD_TASK:
        await new_task_prompt(message, account, state)
    elif name == "newproject":
        await ask_project_name(message, account, state)
    elif name == "task":
        if not command.args:
            await message.answer("Use /tasks or /projects and choose a task.")
        else:
            await show_task(message, account, uuid.UUID(command.args.strip()))
    elif name == "due" or message.text == DUE:
        tasks = await due_tasks(account)
        await show_list(message, tasks, "Due within four days / overdue · incomplete tasks")
    else:
        await show_projects(message, account)


async def handle_task_project(message, account, state, data):
    choice = (message.text or "").strip().casefold()
    projects = await asyncio.to_thread(ws.list_projects, account)
    matches = [p for p in projects if p["can_manage"] and p["name"].casefold() == choice]
    if choice in {"personal", "personal task"} or len(matches) == 1:
        await state.clear()
        if choice not in {"personal", "personal task"}:
            await state.update_data(project_id=str(matches[0]["id"]))
        await state.set_state(WorkFlow.task_title)
        await message.answer(TITLE_PROMPT, reply_markup=cancel_keyboard())
        return
    await message.answer(
        "Choose a project or Personal task using the buttons above, "
        "or send its name as text or a voice note. /cancel stops.",
        reply_markup=cancel_keyboard(),
    )


async def handle_project_name(message, account, state, data):
    project = await asyncio.to_thread(ws.create_project, account, message.text or "")
    await state.clear()
    await show_project(message, account, project["id"])


async def handle_quick_voice(message, account, state, data):
    task = await asyncio.to_thread(
        ws.create_task,
        account,
        message.text or "",
        source=data.get("source", "text"),
        chat_id=message.chat.id,
        message_id=message.message_id,
    )
    await state.clear()
    await show_task(message, account, task["id"])


async def handle_member_email(message, account, state, data):
    await asyncio.to_thread(
        ws.add_member, account, uuid.UUID(data["project_id"]), message.text or ""
    )
    await state.clear()
    await message.answer("Person added to the project.")
    await show_project(message, account, uuid.UUID(data["project_id"]))


async def handle_task_title(message, account, state, data):
    media = message.voice or message.audio
    if media:
        if voice_too_large(media):
            await message.answer(VOICE_LIMIT, reply_markup=cancel_keyboard())
            return
        # The transcription worker (not this bot process) handles the recording, then
        # advances this draft to the description step if it is still waiting for it.
        await state.update_data(voice_title_message=message.message_id)
        await asyncio.to_thread(
            queue_voice_title,
            message.from_user.id,
            message.from_user.full_name,
            message.chat.id,
            message.message_id,
            media.file_id,
        )
        await message.answer(
            "🎙 Got it — transcribing your title… I'll show it here in a moment.\n"
            "You can also type the title instead, or /cancel.",
            reply_markup=cancel_keyboard(),
        )
        return

    title = " ".join((message.text or "").split())
    if not title or len(title) > 120:
        await message.answer(
            "📝 Please type a task title using 1–120 characters, or send a voice note, or /cancel.",
            reply_markup=cancel_keyboard(),
        )
        return
    # A typed title supersedes any recording still being transcribed.
    await state.update_data(title=title, voice_title_message=None)
    await state.set_state(WorkFlow.task_content)
    rows = [
        [("⏩ Skip description", "tasknew:nodescription")],
        [("Cancel", "tasknew:cancel")],
    ]
    await message.answer(
        "📄 Step 2 · Task description\n"
        "Send the full details as text or a voice note, "
        "or tap <b>Skip description</b> to use only the title.\n"
        "You’ll choose a deadline before saving.",
        reply_markup=keyboard(rows),
        parse_mode="HTML",
    )


async def handle_task_content(message, account, state, data):
    media = message.voice or message.audio
    if media:
        if voice_too_large(media):
            await message.answer(VOICE_LIMIT, reply_markup=cancel_keyboard())
            return
        await state.update_data(file_id=media.file_id)
    elif message.text and message.text.strip():
        if len(message.text) > 10000:
            await message.answer(
                "Please keep the description within 10,000 characters.",
                reply_markup=cancel_keyboard(),
            )
            return
        await state.update_data(text=message.text)
    else:
        await message.answer(
            "📄 Send the task description as text or a voice note, or /cancel.",
            reply_markup=cancel_keyboard(),
        )
        return
    await state.update_data(message_id=message.message_id, chat_id=message.chat.id)
    await finish_description(message, account, state, data)


async def handle_task_assignee(message, account, state, data):
    people = await asyncio.to_thread(ws.members, account, uuid.UUID(data["project_id"]))
    value = (message.text or "").strip().casefold()
    if value in {"unassigned", "none", "no one"}:
        await choose_assignee(message, account, state, None)
        return
    if value == "done" and data.get("edit_task"):
        await state.clear()
        await show_task(message, account, uuid.UUID(data["edit_task"]))
        return
    matches = [
        p for p in people if p["name"].casefold() == value or p["email"] == spoken_email(value)
    ]
    if len(matches) != 1:
        await message.answer(
            "Choose a person using the buttons, or send a project member’s name or email "
            "as text or a voice note. Use an email if names are the same.",
            reply_markup=cancel_keyboard(),
        )
        return
    await choose_assignee(message, account, state, matches[0]["id"])


async def handle_due(message, account, state, data):
    try:
        deadline = parse_due(message.text or "")
    except (ValueError, ws.WorkspaceError) as error:
        reason = f"{error.message}. " if isinstance(error, ws.WorkspaceError) else ""
        await message.answer(
            f"{reason}Choose a date in the calendar above, or type YYYY-MM-DD, "
            "YYYY-MM-DD HH:MM, or - for no deadline. "
            "You can also send a voice note, for example ‘tomorrow at six pm’.",
        )
        return
    await apply_deadline(message, account, state, deadline)


async def handle_comment(message, account, state, data):
    identifier = uuid.UUID(data["task_id"])
    media = message.voice or message.audio
    if media:
        if voice_too_large(media):
            await message.answer(VOICE_LIMIT)
            return
        await asyncio.to_thread(
            queue_voice,
            message.from_user.id,
            message.from_user.full_name,
            message.chat.id,
            message.message_id,
            media.file_id,
            comment_task_id=identifier,
        )
        await state.clear()
        await message.answer("🎙 Voice comment queued. I’ll transcribe it and add it to this task.")
        return
    if not message.text or not message.text.strip():
        await message.answer("Send a text comment or a voice note, or /cancel.")
        return
    await asyncio.to_thread(
        ws.add_comment,
        account,
        identifier,
        message.text or "",
        message.chat.id,
        message.message_id,
    )
    await state.clear()
    await show_task(message, account, identifier)


async def handle_edit_title(message, account, state, data):
    await edit_task_field(message, account, state, data, editing_title=True)


async def handle_edit_text(message, account, state, data):
    await edit_task_field(message, account, state, data, editing_title=False)


async def edit_task_field(message, account, state, data, editing_title):
    field = "title" if editing_title else "description"
    media = message.voice or message.audio
    if media:
        if voice_too_large(media):
            await message.answer(VOICE_LIMIT, reply_markup=cancel_keyboard())
            return
        identifier = uuid.UUID(data["edit_task"])
        # Saved durably first; the transcription worker applies it with update_task's checks.
        await asyncio.to_thread(
            queue_voice_edit,
            message.from_user.id,
            message.from_user.full_name,
            message.chat.id,
            message.message_id,
            media.file_id,
            identifier,
            "title" if editing_title else "text",
        )
        await state.clear()
        await message.answer(
            f"🎙 Got it — transcribing your new {field}… "
            "I'll update the task and confirm here when it's ready."
        )
        return
    text = " ".join((message.text or "").split()) if editing_title else message.text or ""
    limit = 120 if editing_title else 10000
    if not text.strip() or len(text) > limit:
        await message.answer(
            f"Please send the new {field} using 1–{limit:,} characters "
            "as text or a voice note, or /cancel.",
            reply_markup=cancel_keyboard(),
        )
        return
    identifier = uuid.UUID(data["edit_task"])
    await asyncio.to_thread(
        ws.update_task, account, identifier, {"title" if editing_title else "text": text}
    )
    await state.clear()
    await show_task(message, account, identifier)


# Each handler receives the FSM data read before it runs: (message, account, state, data).
STATE_HANDLERS = {
    WorkFlow.task_project.state: handle_task_project,
    WorkFlow.project_name.state: handle_project_name,
    WorkFlow.member_email.state: handle_member_email,
    WorkFlow.task_title.state: handle_task_title,
    WorkFlow.task_content.state: handle_task_content,
    WorkFlow.task_assignee.state: handle_task_assignee,
    WorkFlow.task_due.state: handle_due,
    WorkFlow.quick_voice.state: handle_quick_voice,
    WorkFlow.edit_due.state: handle_due,
    WorkFlow.comment.state: handle_comment,
    WorkFlow.edit_title.state: handle_edit_title,
    WorkFlow.edit_text.state: handle_edit_text,
}


async def conversation(message, account, state):
    current = await state.get_state()
    if message.text and (message.text.startswith("/") or message.text == MENU):
        await state.clear()
        if message.text == MENU:
            await show_main_menu(message, "Cancelled. Choose an action:")
            return
        await message.answer(CANCELLED)
        return
    data = await state.get_data()
    pending = data.get(VOICE_ANSWER) or {}
    if pending.get("text"):
        await message.answer(
            "Approve this transcript, or tap Cancel to answer again by voice or typing.",
            reply_markup=review_messages(pending)[-1]["reply_markup"],
        )
        return
    if current in INPUT_STATES and (message.voice or message.audio):
        await collect_voice_answer(message, account, state, current, data)
        return
    if data.get(VOICE_ANSWER):
        await state.update_data(**{VOICE_ANSWER: None})
    handler = STATE_HANDLERS.get(current)
    if handler:
        await handler(message, account, state, data)


async def collect_voice_answer(message, account, state, current, data):
    media = message.voice or message.audio
    if voice_too_large(media):
        await message.answer(VOICE_LIMIT, reply_markup=cancel_keyboard())
        return
    if current in {WorkFlow.edit_title.state, WorkFlow.edit_text.state, WorkFlow.edit_due.state}:
        task = await asyncio.to_thread(ws.get_task, account, uuid.UUID(data["edit_task"]))
        if task.get("archived_at"):
            raise ws.WorkspaceError(409, "Restore this task before editing it")
        if not task["can_manage"]:
            raise ws.WorkspaceError(403, "Only the project manager can edit this task")
    pending = data.get(VOICE_ANSWER) or {}
    if pending.get("message_id") != message.message_id or pending.get("state") != current:
        pending = {
            "token": uuid.uuid4().hex,
            "state": current,
            "message_id": message.message_id,
            "owner_id": str(account.id),
            "editor_token": secrets.token_urlsafe(32),
        }
    pending.setdefault("editor_token", secrets.token_urlsafe(32))
    await state.update_data(**{VOICE_ANSWER: pending})
    await asyncio.to_thread(
        queue_voice_input,
        message.from_user.id,
        message.from_user.full_name,
        message.chat.id,
        message.message_id,
        media.file_id,
        pending["token"],
    )
    await message.answer(
        f"🎙 Got it — transcribing your {INPUT_STATES[current]}… "
        "I’ll show it here for you to confirm. You can also type your answer, or /cancel.",
        reply_markup=cancel_keyboard(),
    )
