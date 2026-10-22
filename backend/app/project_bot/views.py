"""Project, task, and assignee screens rendered as inline-keyboard messages."""

import asyncio
import uuid

from aiogram.exceptions import TelegramAPIError

from app import workspace as ws
from app.bot_ui import MENU
from app.project_bot.common import LIST_LIMIT, MEMBER_LIMIT, PROJECT_PAGE, WorkFlow, keyboard
from app.telegram_format import safe, status_label, task_button, task_details, task_summary


async def due_tasks(account):
    return await asyncio.to_thread(
        ws.list_tasks, account, None, account.role == "developer", True, LIST_LIMIT
    )


async def show_projects(message, account, page=0):
    projects = await asyncio.to_thread(ws.list_projects, account)
    page = max(0, min(page, max(0, (len(projects) - 1) // PROJECT_PAGE)))
    items = projects[page * PROJECT_PAGE : (page + 1) * PROJECT_PAGE]
    rows = [[(f"📁 {p['name'][:57]}", f"project:{p['id']}")] for p in items]
    navigation = []
    if page:
        navigation.append(("← Previous", f"projects:{page - 1}"))
    if (page + 1) * PROJECT_PAGE < len(projects):
        navigation.append(("Next →", f"projects:{page + 1}"))
    if navigation:
        rows.append(navigation)
    action_row = []
    if ws.manager(account):
        action_row.append(("＋ New project", "project:new"))
    action_row.append((MENU, "menu:main"))
    rows.append(action_row)
    pages = f" · Page {page + 1}" if len(projects) > PROJECT_PAGE else ""
    await message.answer(
        f"📁 <b>Your projects</b>{pages}\nChoose a project to view its tasks."
        if projects
        else "📁 <b>No projects yet</b>\nAssigned projects appear here.",
        reply_markup=keyboard(rows) if rows else None,
        parse_mode="HTML",
    )


async def new_task_prompt(message, account, state):
    await state.clear()
    projects = await asyncio.to_thread(ws.list_projects, account)
    managed = [project for project in projects if project["can_manage"]]
    rows = [[(project["name"][:60], f"projectadd:{project['id']}")] for project in managed[:38]]
    has_projects = bool(rows)
    more = (
        f"\n\nShowing the first 38 of {len(managed)} projects. "
        "Use /projects and Add task to choose another project."
        if len(managed) > 38
        else ""
    )
    rows.append([("Personal task", "tasknew:personal")])
    rows.append([("Cancel", "tasknew:cancel")])
    await state.set_state(WorkFlow.task_project)
    await message.answer(
        "Choose a project for this task, or choose Personal task. "
        "You can also type or say the name in a voice note.\n\n"
        "Next, enter a short title (or send a voice note directly), then details." + more
        if has_projects
        else "Choose Personal task to get started, or type or say ‘Personal task’.\n\n"
        "Projects you manage will appear here when available.",
        reply_markup=keyboard(rows),
    )


async def show_list(message, tasks, title, page=0, prefix="due"):
    page = max(0, min(page, max(0, (len(tasks) - 1) // 5)))
    items = tasks[page * 5 : page * 5 + 5]
    heading = "⏰" if prefix == "due" else "📁"
    lines = [f"{heading} <b>{safe(title, 120)}</b> · Page {page + 1}"]
    rows = []
    for number, task in enumerate(items, page * 5 + 1):
        lines.append(task_summary(task, number))
        rows.append([(task_button(task, number), f"task:{task['id']}")])
    if not items:
        lines.append("📭 No tasks here yet.")
    else:
        lines.append("👇 Tap a task below to open its details.")
    if len(tasks) >= LIST_LIMIT:
        lines.append(f"Showing the newest {LIST_LIMIT} tasks. Use the website for older ones.")
    navigation = []
    if page:
        navigation.append(("← Previous", f"{prefix}:{page - 1}"))
    if (page + 1) * 5 < len(tasks):
        navigation.append(("Next →", f"{prefix}:{page + 1}"))
    if navigation:
        rows.append(navigation)
    nav = []
    if prefix.startswith("projectpage"):
        nav.append(("🗂 Projects", "menu:projects"))
    else:
        nav.append(("📋 My tasks", "menu:tasks"))
    nav.append((MENU, "menu:main"))
    rows.append(nav)
    await message.answer(
        "\n".join(lines), reply_markup=keyboard(rows) if rows else None, parse_mode="HTML"
    )


async def show_project(message, account, identifier, page=0):
    projects = await asyncio.to_thread(ws.list_projects, account)
    project = next((p for p in projects if p["id"] == identifier), None)
    # Access is rechecked even for callbacks from old project menus.
    tasks = await asyncio.to_thread(ws.list_tasks, account, identifier, limit=LIST_LIMIT)
    await show_list(
        message,
        tasks,
        f"Project: {project['name']}" if project else "Project",
        page,
        f"projectpage:{identifier}",
    )
    if project and project["can_manage"]:
        await message.answer(
            "Manage this project:",
            reply_markup=keyboard(
                [
                    [
                        ("＋ Add task", f"projectadd:{identifier}"),
                        ("Add person", f"projectmember:{identifier}"),
                    ],
                    [
                        ("🗂 Projects", "menu:projects"),
                        (MENU, "menu:main"),
                    ],
                ]
            ),
        )


async def show_task(message, account, identifier):
    task = await asyncio.to_thread(ws.get_task, account, identifier)
    comments = await asyncio.to_thread(ws.comments, account, identifier)
    rows = []
    if task["can_move"]:
        rows.append(
            [
                (status_label(status), f"taskstatus:{identifier}:{status}")
                for status in ("pending", "in_progress", "completed")
            ]
        )
    rows.append([("💬 Comment", f"taskcomment:{identifier}"), ("🔄 Refresh", f"task:{identifier}")])
    if task["can_manage"]:
        controls = [("📅 Set deadline", f"taskdue:{identifier}")]
        if task["project_id"]:
            controls.append(("👤 Assign person", f"taskassign:{identifier}"))
        rows.append(controls)
        rows.append(
            [
                ("✏️ Edit title", f"taskedittitle:{identifier}"),
                ("✏️ Edit description", f"taskedittext:{identifier}"),
            ]
        )
        rows.append([("🗑 Delete", f"taskdelete:{identifier}")])
    rows.append([("📋 My tasks", "menu:tasks"), (MENU, "menu:main")])
    await message.answer(
        task_details(task, comments), reply_markup=keyboard(rows), parse_mode="HTML"
    )


async def assignee_prompt(message, account, state, replace=False):
    data = await state.get_data()
    people = await asyncio.to_thread(ws.members, account, uuid.UUID(data["project_id"]))
    await state.set_state(WorkFlow.task_assignee)
    chosen = set()
    if data.get("edit_task"):
        # Editing toggles people on the existing task, so show who is assigned now.
        task = await asyncio.to_thread(ws.get_task, account, uuid.UUID(data["edit_task"]))
        chosen = {str(identifier) for identifier in task["assignee_ids"]}
        names = ", ".join(person["name"] for person in task["assignees"]) or "Unassigned"
        text = (
            f"👤 Assigned to: {names}\n\n"
            "Tap a person to add or remove them (✓ = assigned), or send a project member’s "
            "name or email as text or a voice note. Unassigned removes everyone. "
            "Tap or say Done when finished."
        )
    else:
        text = (
            "Choose who will do this task, or send a project member’s name or email "
            "as text or a voice note. /cancel stops."
        )
    if len(people) > MEMBER_LIMIT:
        text += (
            f"\n\nShowing the first {MEMBER_LIMIT} of {len(people)} people. "
            "Type or say an email to choose someone else."
        )
    rows = [
        [
            (
                ("✓ " if str(p["id"]) in chosen else "") + f"{p['name']} · {p['email']}"[:60],
                f"assignee:{p['id']}",
            )
        ]
        for p in people[:MEMBER_LIMIT]
    ]
    rows.append([("Unassigned", "assignee:none")])
    if data.get("edit_task"):
        rows.append([("✓ Done", "assignee:done")])
    else:
        rows.append([("Cancel", "tasknew:cancel")])
    if replace:
        try:
            await message.edit_text(text, reply_markup=keyboard(rows))
            return
        except TelegramAPIError as error:
            if "message is not modified" in str(error).lower():
                return
    await message.answer(text, reply_markup=keyboard(rows))
