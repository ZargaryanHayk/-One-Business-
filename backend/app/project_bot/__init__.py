"""Project conversations and task controls, using the same service as the website.

common    states, keyboards, and input parsing
views     project, task, and assignee screens
wizard    saving the new-task draft and its assignee and deadline steps
messages  commands and replies, dispatched by FSM state
callbacks inline buttons, dispatched by callback data
"""

import logging

from aiogram import F
from aiogram.filters import Command, StateFilter
from aiogram.types import CallbackQuery

from app import workspace as ws
from app.bot_ui import ADD_TASK
from app.project_bot.callbacks import CALLBACK_PREFIXES, MENU_CALLBACKS, callback
from app.project_bot.common import DUE, PROJECTS, WorkFlow, due_label, keyboard, parse_due
from app.project_bot.messages import conversation, project_command
from app.project_bot.views import assignee_prompt, show_list, show_project, show_projects, show_task
from app.project_bot.wizard import apply_deadline, choose_assignee

__all__ = [
    "DUE",
    "PROJECTS",
    "WorkFlow",
    "apply_deadline",
    "assignee_prompt",
    "callback",
    "choose_assignee",
    "conversation",
    "due_label",
    "guarded",
    "keyboard",
    "parse_due",
    "project_command",
    "register",
    "show_list",
    "show_project",
    "show_projects",
    "show_task",
]

log = logging.getLogger(__name__)


def guarded(handler):
    async def run(event, account, state, command=None):
        message = event.message if isinstance(event, CallbackQuery) else event
        try:
            if handler is project_command:
                await handler(event, account, state, command)
            else:
                await handler(event, account, state)
        except ws.WorkspaceError as error:
            await message.answer(error.message)
        except (ValueError, IndexError):
            await message.answer("Invalid selection. Use /projects or /tasks to refresh.")
        except Exception as error:
            log.warning("project_bot_failed", extra={"error_type": type(error).__name__})
            await message.answer("Could not complete this action. Please try again.")

    return run


def register(router):
    router.message.register(
        guarded(project_command), Command("projects", "newproject", "newtask", "due", "task")
    )
    router.message.register(guarded(project_command), F.text.in_({PROJECTS, DUE, ADD_TASK}))
    router.message.register(guarded(conversation), StateFilter(*WorkFlow.__all_states__))
    router.callback_query.register(guarded(callback), F.data.startswith(CALLBACK_PREFIXES))
    router.callback_query.register(guarded(callback), F.data.in_(set(MENU_CALLBACKS)))
