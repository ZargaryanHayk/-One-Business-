from aiogram.types import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.telegram_format import task_button, task_summary

MENU = "🏠 Menu"
TASKS = "📋 My tasks"
ADD_TASK = "＋ Add task"
LOGIN = "🌐 Log in / Dashboard"
LOGOUT = "🚪 Log out"
HELP = "❓ Help"
SIGN_IN = "🔐 Log in"
MENU_ROWS = (
    ((ADD_TASK, "menu:newtask"), ("⏰ Due soon", "menu:due")),
    ((HELP, "menu:help"), (TASKS, "menu:tasks")),
    ((LOGOUT, "menu:logout_prompt"), ("🗂 Projects", "menu:projects")),
)

PUBLIC_COMMANDS = [
    BotCommand(command="start", description="Start and log in"),
    BotCommand(command="login", description="Log in with email and password"),
]

COMMANDS = [
    BotCommand(command="menu", description="Show the main menu"),
    BotCommand(command="start", description="Show the main menu"),
    BotCommand(command="tasks", description="Browse your tasks"),
    BotCommand(command="newtask", description="Create a personal or project task"),
    BotCommand(command="projects", description="Projects and assigned tasks"),
    BotCommand(command="newproject", description="Create a project (managers)"),
    BotCommand(command="due", description="Incomplete tasks due within four days"),
    BotCommand(command="login", description="Log in or open your dashboard"),
    BotCommand(command="dashboard", description="Open your web dashboard"),
    BotCommand(command="logout", description="Sign out of your web dashboard sessions"),
    BotCommand(command="help", description="How to create and manage tasks"),
]


def login_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=SIGN_IN, callback_data="auth:login"),
            ]
        ]
    )


def inline_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=action) for label, action in row]
            for row in MENU_ROWS
        ]
    )


def main_keyboard() -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=label) for label, _ in row] for row in MENU_ROWS]
    rows.append([KeyboardButton(text=MENU)])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Type a task, record a voice note, or choose a button",
    )


async def show_main_menu(message, text: str = "Choose an action:") -> None:
    # Telegram requires separate messages to attach reply and inline keyboards.
    # Both use MENU_ROWS so their labels and ordering stay consistent.
    await message.answer("Your task menu is ready.", reply_markup=main_keyboard())
    await message.answer(text, reply_markup=inline_menu())


def tasks_keyboard(page: int, has_next: bool, tasks=None) -> InlineKeyboardMarkup:
    navigation = []
    if page:
        navigation.append(
            InlineKeyboardButton(text="← Previous", callback_data=f"tasks:{page - 1}")
        )
    if has_next:
        navigation.append(InlineKeyboardButton(text="Next →", callback_data=f"tasks:{page + 1}"))
    rows = [navigation] if navigation else []
    if tasks:
        rows.extend(
            [InlineKeyboardButton(text=task_button(task, number), callback_data=f"task:{task.id}")]
            for number, task in enumerate(tasks, page * 5 + 1)
        )
    rows.append(
        [
            InlineKeyboardButton(text="Refresh", callback_data=f"tasks:{page}"),
            InlineKeyboardButton(text=ADD_TASK, callback_data="menu:newtask"),
        ]
    )
    rows.append([InlineKeyboardButton(text=MENU, callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def logout_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Log out of Telegram only", callback_data="menu:logout_telegram"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Log out of all website sessions", callback_data="menu:logout"
                ),
            ],
            [InlineKeyboardButton(text="Cancel", callback_data="menu:cancel")],
        ]
    )


def tasks_text(tasks, page: int) -> str:
    if not tasks:
        return "📭 <b>No tasks yet</b>\n\nTap ＋ Add task to choose a project and get started."
    heading = f"📋 <b>My tasks</b> · Page {page + 1}"
    rows = "\n".join(task_summary(task, number) for number, task in enumerate(tasks, page * 5 + 1))
    return f"{heading}\n\n{rows}\n\n👇 Tap a task below for details, status changes, and comments."
