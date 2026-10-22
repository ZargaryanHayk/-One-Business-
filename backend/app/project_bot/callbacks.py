"""Inline-button handlers, dispatched by their callback data."""

import asyncio
import uuid

from aiogram.exceptions import TelegramAPIError

from app import workspace as ws
from app.bot_ui import show_main_menu
from app.deadline_picker import close_picker, handle_picker, open_picker
from app.project_bot.common import (
    CANCELLED,
    DEADLINE_STATES,
    TITLE_PROMPT,
    WorkFlow,
    cancel_keyboard,
    due_label,
    keyboard,
    require_project_manager,
)
from app.project_bot.messages import conversation
from app.project_bot.views import (
    assignee_prompt,
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
from app.voice_input import INPUT_STATES, VOICE_ANSWER

# Buttons that belong to the conversation in progress and so keep its FSM state.
KEEP_STATE_PREFIXES = ("tasknew:", "assignee:")


async def deadline_callback(query, account, state):
    result = await handle_picker(query, state, DEADLINE_STATES)
    if result.action == "cancel":
        await state.clear()
        await close_picker(
            query.message, "Deadline selection cancelled. No task changes were made."
        )
    elif result.action in {"save", "none"}:
        await apply_deadline(query.message, account, state, result.deadline, sender=query.from_user)
        await close_picker(query.message, f"Deadline selected: {due_label(result.deadline)}")


async def voice_input_callback(query, account, state):
    parts = query.data.split(":")
    current = await state.get_state()
    pending = (await state.get_data()).get(VOICE_ANSWER) or {}
    if (
        len(parts) not in {3, 4}
        or current not in INPUT_STATES
        or current != pending.get("state")
        or parts[1] != pending.get("token")
        or pending.get("owner_id") != str(account.id)
        or not pending.get("text")
        or parts[2] not in {"use", "approve", "cancel", "again"}
        or (
            parts[2] in {"approve", "cancel"}
            and (len(parts) != 4 or parts[3] != str(pending.get("revision", 0)))
        )
        or (parts[2] in {"use", "again"} and pending.get("revision", 0) != 0)
    ):
        await query.answer("This spoken answer expired. Send your answer again.")
        return
    await query.answer()
    if parts[2] in {"cancel", "again"}:
        await state.update_data(**{VOICE_ANSWER: None})
        await state.set_state(current)
        if current in {WorkFlow.task_content.state, WorkFlow.quick_voice.state}:
            await state.update_data(source="text", file_id=None)
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except TelegramAPIError:
            pass
        await query.message.answer(
            f"Transcript cancelled. Send your {INPUT_STATES[current]} again "
            "as a voice note or type it. /cancel exits.",
            reply_markup=cancel_keyboard(),
        )
        return
    await state.update_data(**{VOICE_ANSWER: None})
    if current in {WorkFlow.task_content.state, WorkFlow.quick_voice.state}:
        await state.update_data(source="voice", file_id=None)
    # Reuse the normal field validation with the original speaker and recording ID.
    message = query.message.model_copy(
        update={
            "from_user": query.from_user,
            "message_id": pending["message_id"],
            "text": pending["text"],
            "voice": None,
            "audio": None,
            "entities": None,
        }
    ).as_(query.bot)
    try:
        await conversation(message, account, state)
    except ws.WorkspaceError:
        if await state.get_state() == current:
            await state.update_data(**{VOICE_ANSWER: pending})
        raise


# Buttons with fixed callback data: (query, account, state).


async def open_main_menu(query, account, state):
    await show_main_menu(query.message)


async def open_new_task(query, account, state):
    await new_task_prompt(query.message, account, state)


async def open_projects(query, account, state):
    await show_projects(query.message, account)


async def open_due(query, account, state):
    tasks = await due_tasks(account)
    await show_list(query.message, tasks, "Due within four days / overdue")


async def cancel_step(query, account, state):
    # This button also cancels project, member, comment, and edit steps.
    await state.clear()
    await query.message.answer(CANCELLED)


async def skip_description(query, account, state):
    data = await state.get_data()
    title = data.get("title") or "Untitled task"
    await state.update_data(
        text=title,
        message_id=query.message.message_id,
        chat_id=query.message.chat.id,
    )
    await finish_description(query.message, account, state, data)


async def start_personal_task(query, account, state):
    await state.clear()
    await state.set_state(WorkFlow.task_title)
    await query.message.answer(TITLE_PROMPT, reply_markup=cancel_keyboard())


MENU_CALLBACKS = {
    "menu:main": open_main_menu,
    "menu:newtask": open_new_task,
    "menu:projects": open_projects,
    "menu:due": open_due,
}
WIZARD_CALLBACKS = {
    "tasknew:cancel": cancel_step,
    "tasknew:nodescription": skip_description,
    "tasknew:personal": start_personal_task,
}


# Buttons whose data is "action:part[:part]": (query, account, state, parts after the action).


async def page_projects(query, account, state, parts):
    await show_projects(query.message, account, int(parts[0]))


async def page_due(query, account, state, parts):
    tasks = await due_tasks(account)
    await show_list(query.message, tasks, "Due within four days / overdue", int(parts[0]))


async def pick_assignee(query, account, state, parts):
    if await state.get_state() != WorkFlow.task_assignee.state:
        await query.message.answer("This selection expired. Open the project or task again.")
        return
    data = await state.get_data()
    if parts[0] == "done" and data.get("edit_task"):
        await state.clear()
        await show_task(query.message, account, uuid.UUID(data["edit_task"]))
        return
    await choose_assignee(
        query.message,
        account,
        state,
        None if parts[0] == "none" else uuid.UUID(parts[0]),
        replace=True,
    )


async def open_project(query, account, state, parts):
    if parts[0] == "new":
        await ask_project_name(query.message, account, state)
    else:
        await open_project_page(query, account, state, parts)


async def open_project_page(query, account, state, parts):
    identifier = uuid.UUID(parts[0])
    await show_project(query.message, account, identifier, int(parts[1]) if len(parts) > 1 else 0)


async def add_project_task(query, account, state, parts):
    await ask_for_project(query, account, state, parts, WorkFlow.task_title, TITLE_PROMPT)


async def add_project_member(query, account, state, parts):
    await ask_for_project(
        query,
        account,
        state,
        parts,
        WorkFlow.member_email,
        "Send the existing account’s email as text or a voice note to add them to this project.",
    )


async def ask_for_project(query, account, state, parts, step, prompt):
    identifier = uuid.UUID(parts[0])
    # Check management permission before asking for any input.
    await asyncio.to_thread(require_project_manager, account, identifier)
    await state.update_data(project_id=str(identifier))
    await state.set_state(step)
    await query.message.answer(prompt, reply_markup=cancel_keyboard())


async def open_task(query, account, state, parts):
    await show_task(query.message, account, uuid.UUID(parts[0]))


async def set_task_status(query, account, state, parts):
    identifier = uuid.UUID(parts[0])
    await asyncio.to_thread(ws.update_task, account, identifier, {"status": parts[1]})
    await show_task(query.message, account, identifier)


async def start_comment(query, account, state, parts):
    identifier = uuid.UUID(parts[0])
    await asyncio.to_thread(ws.get_task, account, identifier)
    await state.clear()
    await state.update_data(task_id=str(identifier))
    await state.set_state(WorkFlow.comment)
    await query.message.answer(
        "💬 Write your comment or send a voice note (up to 5 minutes and 20 MB), or /cancel.",
        reply_markup=cancel_keyboard(),
    )


async def managed_task(account, identifier):
    task = await asyncio.to_thread(ws.get_task, account, identifier)
    if not task["can_manage"]:
        raise ws.WorkspaceError(403, "Only the project manager can edit this task")
    return task


async def confirm_delete(query, account, state, parts):
    identifier = uuid.UUID(parts[0])
    await managed_task(account, identifier)
    await query.message.answer(
        "Delete this task?",
        reply_markup=keyboard(
            [[("Delete task", f"taskremove:{identifier}"), ("Keep task", f"task:{identifier}")]]
        ),
    )


async def delete_task(query, account, state, parts):
    identifier = uuid.UUID(parts[0])
    await managed_task(account, identifier)
    await asyncio.to_thread(ws.delete_task, account, identifier)
    await query.message.answer("Task deleted.")


async def edit_title(query, account, state, parts):
    await ask_for_edit(
        query,
        account,
        state,
        parts,
        WorkFlow.edit_title,
        "📝 Send the new task title (1–120 characters) as text or a voice note "
        "(up to 5 minutes and 20 MB), or /cancel:",
    )


async def edit_text(query, account, state, parts):
    await ask_for_edit(
        query,
        account,
        state,
        parts,
        WorkFlow.edit_text,
        "📄 Send the new task description as text or a voice note "
        "(up to 5 minutes and 20 MB), or /cancel:",
    )


async def ask_for_edit(query, account, state, parts, step, prompt):
    identifier = uuid.UUID(parts[0])
    await managed_task(account, identifier)
    await state.clear()
    await state.update_data(edit_task=str(identifier))
    await state.set_state(step)
    await query.message.answer(prompt, reply_markup=cancel_keyboard())


async def edit_assignees(query, account, state, parts):
    identifier = uuid.UUID(parts[0])
    task = await managed_task(account, identifier)
    await remember_task(state, identifier, task)
    await assignee_prompt(query.message, account, state)


async def edit_deadline(query, account, state, parts):
    identifier = uuid.UUID(parts[0])
    task = await managed_task(account, identifier)
    await remember_task(state, identifier, task)
    await state.set_state(WorkFlow.edit_due)
    await open_picker(query.message, state, task["due_at"])


async def remember_task(state, identifier, task):
    await state.clear()
    await state.update_data(
        edit_task=str(identifier),
        project_id=str(task["project_id"]) if task["project_id"] else None,
    )


ACTION_CALLBACKS = {
    "projects": page_projects,
    "due": page_due,
    "assignee": pick_assignee,
    "project": open_project,
    "projectpage": open_project_page,
    "projectadd": add_project_task,
    "projectmember": add_project_member,
    "task": open_task,
    "taskstatus": set_task_status,
    "taskcomment": start_comment,
    "taskdelete": confirm_delete,
    "taskremove": delete_task,
    "taskedittitle": edit_title,
    "taskedittext": edit_text,
    "taskassign": edit_assignees,
    "taskdue": edit_deadline,
}
# Every prefix routed to callback(); "dl:" is the deadline picker's own callback data.
CALLBACK_PREFIXES = (
    "voiceinput:",
    "dl:",
    "tasknew:",
    *(f"{action}:" for action in ACTION_CALLBACKS),
)


async def callback(query, account, state):
    if query.data.startswith("voiceinput:"):
        await voice_input_callback(query, account, state)
        return
    if (await state.get_data()).get(VOICE_ANSWER):
        await state.update_data(**{VOICE_ANSWER: None})
    if query.data.startswith("dl:"):
        await deadline_callback(query, account, state)
        return
    if (
        query.data == "tasknew:nodescription"
        and await state.get_state() != WorkFlow.task_content.state
    ):
        await query.answer("This step expired.")
        return
    await query.answer()
    if not query.data.startswith(KEEP_STATE_PREFIXES):
        # Any other button leaves the current conversation, so later typed text
        # cannot be applied to a task or project the person navigated away from.
        await state.clear()
    fixed = MENU_CALLBACKS.get(query.data) or WIZARD_CALLBACKS.get(query.data)
    if fixed:
        await fixed(query, account, state)
        return
    action, *parts = query.data.split(":")
    handler = ACTION_CALLBACKS.get(action)
    if handler:
        await handler(query, account, state, parts)
    else:
        # Unknown actions are ignored, but a malformed ID still reports an invalid selection.
        uuid.UUID(parts[0])
