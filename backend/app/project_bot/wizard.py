"""Steps of the new-task wizard that save the draft collected in the FSM data."""

import asyncio
import uuid

from app import workspace as ws
from app.deadline_picker import open_picker
from app.domain import queue_voice
from app.project_bot.common import WorkFlow, cancel_keyboard
from app.project_bot.views import assignee_prompt, show_task


def optional_uuid(value):
    return uuid.UUID(value) if value else None


async def ask_project_name(message, account, state):
    if not ws.manager(account):
        raise ws.WorkspaceError(403, "Only project managers can create projects")
    await state.set_state(WorkFlow.project_name)
    await message.answer(
        "Send the project name as text or a voice note, or /cancel.",
        reply_markup=cancel_keyboard(),
    )


async def finish_description(message, account, state, data):
    # Personal tasks go straight to the deadline; project tasks choose assignees first.
    if data.get("project_id"):
        await assignee_prompt(message, account, state)
    else:
        await state.set_state(WorkFlow.task_due)
        await open_picker(message, state)


async def choose_assignee(message, account, state, assignee_id, replace=False):
    data = await state.get_data()
    if data.get("edit_task"):
        task_id = uuid.UUID(data["edit_task"])
        identifiers = []
        if assignee_id is not None:
            task = await asyncio.to_thread(ws.get_task, account, task_id)
            identifiers = list(task["assignee_ids"])
            if assignee_id in identifiers:
                identifiers.remove(assignee_id)
            else:
                identifiers.append(assignee_id)
        # Sending the full list keeps everyone else assigned while toggling one person.
        await asyncio.to_thread(ws.update_task, account, task_id, {"assignee_ids": identifiers})
        await assignee_prompt(message, account, state, replace)
    else:
        await state.update_data(assignee_id=str(assignee_id) if assignee_id else None)
        await state.set_state(WorkFlow.task_due)
        await open_picker(message, state)


async def apply_deadline(message, account, state, deadline, sender=None):
    current = await state.get_state()
    data = await state.get_data()
    sender = sender or message.from_user
    if current == WorkFlow.edit_due.state:
        identifier = uuid.UUID(data["edit_task"])
        await asyncio.to_thread(ws.update_task, account, identifier, {"due_at": deadline})
        await state.clear()
        await show_task(message, account, identifier)
    elif data.get("file_id"):
        await queue_voice_draft(message, state, data, deadline, sender)
    else:
        await create_text_draft(message, account, state, data, deadline)


async def queue_voice_draft(message, state, data, deadline, sender):
    prompt = (
        "🎙 Voice description received. Queuing transcription with your task title."
        if data.get("title")
        else "🎙 Voice note received. Queuing transcription for your task."
    )
    # Save the job before acknowledging so a failed reply cannot lose the recording.
    await asyncio.to_thread(
        queue_voice,
        sender.id,
        sender.full_name,
        data["chat_id"],
        data["message_id"],
        data["file_id"],
        optional_uuid(data.get("project_id")),
        optional_uuid(data.get("assignee_id")),
        deadline,
        title=data.get("title"),
    )
    await state.clear()
    await message.answer(prompt)


async def create_text_draft(message, account, state, data, deadline):
    task = await asyncio.to_thread(
        ws.create_task,
        account,
        data["text"],
        "pending",
        optional_uuid(data.get("project_id")),
        optional_uuid(data.get("assignee_id")),
        deadline,
        data.get("source", "text"),
        data["chat_id"],
        data["message_id"],
        title=data.get("title"),
    )
    await state.clear()
    await show_task(message, account, task["id"])
