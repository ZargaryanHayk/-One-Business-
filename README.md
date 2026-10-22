# Taskline · AI Task Bot

A React web dashboard and Telegram bot for the same account's tasks. Log in with email and
password, create tasks on the website or with Telegram text/voice, and manage **Pending → In Progress → Completed**
on a live Kanban board. PostgreSQL stores accounts and tasks, Redis/Celery handle background
work, and multilingual faster-whisper transcribes voice locally on CPU.

## Start the application

Requirements: Docker with Compose 2.24+, a Telegram bot token from
[@BotFather](https://t.me/BotFather), and internet access for Telegram and initial downloads.
Allocate approximately 4 CPU cores, 6 GB RAM, and several GB of disk to Docker.

```bash
cp .env.example .env
# Set TELEGRAM_BOT_TOKEN in .env.
docker compose up --build -d
docker compose ps -a
```

The one-shot migration service creates the schema and seeds these two assessment accounts:

| Email | Password | Role |
|---|---|---|
| `test1@hire.me` | `hireme1` | Project manager |
| `test2@hire.me` | `hireme2` | Developer |

Only Argon2id password hashes are stored in PostgreSQL and the migrations. Migration
`0006` corrects the original developer test password to `hireme2`, and migration `0016`
corrects the project manager account to `test1@hire.me` / `hireme1`; both preserve
independently changed passwords. Migrations run once; restarting services does not reset passwords or
duplicate the accounts.

The first startup downloads and caches the multilingual `small` Whisper model before the
voice worker starts. This can take several minutes. Inspect progress with:

```bash
docker compose logs -f model-download transcription-worker
```

Only `127.0.0.1:8080` is exposed. PostgreSQL, Redis, and the API stay inside the Docker network.

## Reviewer guide: Telegram bot and the two test accounts

The bot is not shared: each reviewer runs it with their own Telegram bot token.

### 1. Create your bot (one time, about two minutes)

1. In Telegram, open [@BotFather](https://t.me/BotFather) and send `/newbot`.
2. Choose a display name and a username ending in `bot` (for example `my_taskline_bot`).
3. BotFather replies with a token such as `123456789:AA...`. Copy it.
4. Put it in `.env` as `TELEGRAM_BOT_TOKEN=123456789:AA...` and run `docker compose up --build -d`.
   If the stack is already running, `docker compose up -d` recreates the bot with the new token.
5. Open `https://t.me/<your_bot_username>` and press **Start**.

Only one process may poll a token. If the bot never answers, check `docker compose logs bot`
and make sure no other copy of the bot (or a webhook) uses the same token.

### 2. The two accounts and their roles

| Account | Password | Role | What this role can do |
|---|---|---|---|
| `test1@hire.me` | `hireme1` | **Project manager** | Create projects, add people to them, create tasks inside projects, assign tasks, set deadlines, edit titles and descriptions, delete tasks, archive projects. Also has personal tasks and can change status and comment. |
| `test2@hire.me` | `hireme2` | **Developer** | Sees personal tasks plus project tasks assigned to them. Can create personal tasks (text or voice), change status (Pending / In Progress / Completed), comment (text or voice), and archive completed tasks. Cannot create projects or assign work (the API returns 403). |

The same accounts work on the website (http://localhost:8080/login) and in Telegram, and both
views show the same tasks in real time.

### 3. Sign in to the bot

Either:

- **From the website (no password typing):** sign in at http://localhost:8080/login and click
  **Open Telegram**. Telegram opens the bot and signs it into the same account.
- **Inside Telegram:** press **Log in** (or send `/login`), send the email, then send the
  password in a separate message. The bot deletes the password message immediately.

To switch accounts in one Telegram chat, press **Log out**, then log in with the other email.
To watch both roles at once, use the website for one account and Telegram for the other, or
two Telegram accounts.

### 4. Suggested walkthrough (about five minutes)

1. **Text task (developer).** Log in to the bot as `test2@hire.me`. Press **Add task** →
   **Personal task**, then send a short title and details. Keep http://localhost:8080 open as
   `test2@hire.me`: the card appears in **Pending** without refreshing.
2. **Voice task.** Press **Add task** → **Personal task** and send a voice note (up to five
   minutes, any language Whisper supports). The bot replies at once that it is transcribing,
   then shows the text with **Cancel** / **Approve**. The task is created only after **Approve**.
3. **Status change.** Press **My tasks**, open a task, and tap **In Progress** or
   **Completed**. The dashboard card moves column live. Dragging the card on the dashboard
   updates the bot's view too.
4. **Manager flow.** Log out and log in as `test1@hire.me`. Press **Projects** → **＋ New
   project** and name it. Open the project → **Add person** → send `test2@hire.me`. Then
   **＋ Add task**: send a title, choose `Test Two` as assignee, and pick a deadline in the
   calendar.
5. **Developer receives it.** Log in as `test2@hire.me` (in Telegram or on the website). The
   assigned task appears under **My tasks** and **Due soon**. Change its status or add a
   **💬 Comment**; the manager sees it live on the dashboard.

Useful commands after login: `/tasks`, `/newtask`, `/dashboard` (one-time website sign-in
link), `/menu`, `/help`, `/logout`, `/cancel`.

## Website login, dashboard, and logout

1. Open **http://localhost:8080/login** in a web browser on the computer running Docker.
2. Enter either test account's email and password.
3. **My Tasks** opens at **http://localhost:8080/dashboard**, showing assigned and personal tasks.
4. New tasks from the same account's Telegram login appear automatically.
5. Choose **Add task**, enter its title, description, and starting status, then **Create task**. Tasks start
   in Pending by default. Dashboard tasks also appear in Telegram's **My tasks** list.
6. Drag a card between **Pending**, **In Progress**, and **Completed** to save its status.
   On touch screens, hold the card's grip briefly and drag. Keyboard users can focus its
   **Move** button, press Space, choose a column with arrow keys, and press Space again.
   Escape cancels. The destination highlights while dragging; failed saves restore the
   previous column and show an error. Status selectors and task search remain available.
   Cards render in batches of 25 per column. Wide screens load more automatically while
   scrolling; on narrow screens (≤850px), where columns stack, each column has a **Load more**
   button so later columns stay reachable. Live updates never collapse an expanded column.
7. Completed tasks show **Archive** for their manager, their assignee, or the owner of a personal task.
   Archiving removes the task from the active board and Telegram lists while keeping its details
   and comments. Use the archive icon beside search to view archived tasks and choose **Restore**
   to return one to Completed.
   Managers can choose **Delete** on their project cards, then **Delete task** to confirm. Everyone
   can delete their own personal cards. This works for Telegram
   text, voice, and dashboard tasks and removes the task from both views. **Keep task** cancels.
8. Choose **Log out** to open a popup over the board. Select **Log out on this device** to
   end this browser session, or **Log out on all devices** to revoke all of the account's
   browser and Telegram sessions and outstanding sign-in links. The top-right **×** or Escape
   cancels. After logout, the login page shows a logged-out confirmation. Direct `/logout`
   links open the same popup.
   Confirming either logout choice also queues cleanup of recent messages in linked Telegram
   chats. Cancel does not clear messages. Saved tasks are retained.

When signed in on the website, **Open Telegram** signs the bot into that same account
using a five-minute, single-use link. Telegram may ask you to press **Start**; no email or
password entry is needed in the bot. Each click replaces this browser's previous link,
and signing out of the issuing browser revokes its unused link. If the bot is using
another account, it asks **Switch accounts?** and uses the link only after **Switch**;
**Keep** leaves the current login unchanged. Link sign-in never moves older Telegram-only
records to another account. Direct bot visits still offer email/password login. A signed-in
bot user can request a five-minute, single-use website sign-in link with `/dashboard`. The
website asks **Sign in with this link?** and signs in only after **Continue**, replacing
that browser's current session; **Cancel** keeps it.

## Projects, assignments, comments, and reminders

Migration `0004` makes the first account (now `test1@hire.me`) a project manager and keeps `test2@hire.me` a developer.
Existing tasks remain personal and are assigned to their current owners.

1. As the manager, choose **New project** in the left sidebar and give the project a name.
2. Click **⋮** beside the project name in the sidebar, then **Info**.
   See the project members and add the developer using **Add person**, with `test2@hire.me`.
   Managers can remove members here too; their tasks stay in the project and keep any other assignees.
   The project owner cannot be removed.
3. Choose **Add task**, enter the task text, open **Assigned to**, check one or more people, and set a deadline.
   Assignees must already belong to the project. Clear the selection to leave a project task unassigned.
   Each selected person sees the shared task in My Tasks and can change its status.
4. As the developer, open **My Tasks** or choose the assigned project in the sidebar.
   A developer's sidebar lists projects containing their non-deleted assigned tasks.
5. Drag assigned cards or use their selectors to change status. Open **Details & comments**
   to discuss a task. Comments show the author's name and timestamp and update live.
6. The manager can edit task text, assignment, and deadline in **Details & comments**.
7. Open **Due soon** for incomplete tasks due within the next 96 hours, including overdue
   tasks. Developers see their own due tasks; managers see due tasks in their accessible workspace.
   A **View** selector provides the same project navigation on narrow screens.
8. To archive a project, open **⋮** beside its name and choose **Archive project**.
   Only the project manager or an admin can archive or restore it. Archived projects leave
   the active sidebar, My Tasks, Due soon, and Telegram task lists. Tasks, assignments,
   statuses, individual task archives, and comments are preserved; reminders pause.
   Choose the archive icon beside **PROJECTS** to open **Archived projects**, view saved
   work without editing, or **Restore** the project. Restoring brings its active tasks back.

| Role | Project permissions |
|---|---|
| Project manager | Create projects; manage their owned projects, people, tasks, assignments, deadlines, and deletion |
| Developer | Read member project boards, comment, move assigned tasks, and archive/restore assigned completed tasks |
| Admin | Manage all project boards; other people's personal tasks remain private |

Roles are checked on the server for every web request and Telegram action. Project membership
controls shared task visibility; it does not grant management rights. Personal tasks remain private.

The outbox dispatcher checks deadlines every minute. It schedules one Telegram reminder per
**task + assignee + deadline**, with database uniqueness protecting repeated scheduler runs.
Changing the deadline or assignee creates a new reminder opportunity. Unassigned project tasks
are skipped. The recipient must have logged into the bot and have an active Telegram session;
if no session exists, scheduling waits until one is available. Logouts/account switches are
rechecked before delivery, along with completion, deletion, reassignment, and rescheduling.
Completed/deleted or stale reminders are suppressed. Reminders cover deadlines up to seven days
overdue; an undelivered reminder is queued again every 30 minutes, at most four times in total.
Failed Telegram requests retry with backoff;
as with other external notifications, a crash after delivery but before acknowledgment can duplicate a send.
No daily repeat reminders are scheduled.

Web deadline controls use the browser's timezone and store UTC. Telegram's inline calendar,
time controls, and reminder text use `APP_TIMEZONE` (default **Asia/Yerevan**). New dates
default to 23:59; editing an existing deadline preserves its local hour and minute.

Telegram supports the same workflow after login:

- **Projects** or `/projects`: open project boards and task details.
- **New project** or `/newproject`: create a project as a manager; send its name as text or voice.
- **Add person** on a project: add an existing account by typed or spoken email.
- **Add task** on a project: send text or voice, choose an assignee, then pick a deadline
  with the inline calendar and time buttons. Choose **Save deadline** to finish creation.
- **Set deadline** on a task: browse months, pick a day, choose the time, and save. **Today**,
  **Tomorrow**, time presets, and hour/minute adjustments are available. **No deadline** clears
  or skips the deadline; **Cancel** leaves the task unchanged (or cancels a new task).
  Calendar buttons are tied to the current five-minute conversation; old or expired buttons
  cannot change a different task. Text dates remain an optional fallback (`YYYY-MM-DD`,
  `YYYYMMDD`, `YYYY-MM-DD HH:MM`, or `-`). A text date without a time uses 23:59. Deadlines
  must fall between 1970 and 2100 on every channel.
- Spoken answers also work when choosing Personal task or a project, choosing an assignee by
  name or email, and entering or changing a deadline. For example, say “test two at hire dot me,”
  “tomorrow at six pm,” or “no deadline.” The background worker transcribes the answer and shows
  exactly **Cancel** and **Approve** before applying it. **Cancel** discards the transcript and
  lets you answer the same question again by voice or typing. Your other draft fields are kept.
  While reviewing a completed transcript, choose one of these buttons before sending an answer.
  Moving to another question or cancelling the transcript invalidates its old buttons.
  Project names, membership, permissions, and deadlines use the same validation as typed input.
  Login email and password remain text fields. Migration `0017` adds these voice input jobs.
- Task controls: status changes, **Comment**, assignment/deadline changes for managers,
  and confirmed deletion. `/task <uuid>` opens a task directly. When editing, **Assign
  person** marks current assignees with ✓; tapping a person (or typing their email) adds or
  removes them, **Unassigned** clears everyone, and **Done** returns to the task. A new task
  takes one assignee.
- **Edit title** and **Edit description** accept text, voice notes, or uploaded audio.
  Recordings are transcribed locally and shown for review; **Approve** replaces the selected field.
  The five-minute/20 MB recording limits apply. Titles are shortened to 120 characters;
  descriptions retain the full transcript up to 10,000 characters. Failed recordings leave
  the task unchanged so you can send another recording, type the edit, or `/cancel`.
- **Comment** accepts text, a voice note, or uploaded audio. Voice comments are queued for
  local transcription and shown with **Cancel** / **Approve**. Approval saves the text on the selected
  task and updates the website. The same five-minute/20 MB recording limits apply;
  comments are limited to 4,000 characters; cancel and re-record or type a shorter answer. Failed
  recordings that may succeed again have an owner-only retry button.
- **Due soon** or `/due`: browse incomplete tasks in the four-day window or overdue.
- `/cancel` (also `/cancel@BotName`): exit a project/task conversation; outside one it replies
  "Nothing to cancel." Tapping any navigation button (Projects, Due soon, My tasks, a task,
  a status, a page) also ends the current conversation, so later text never lands on an
  abandoned task. Conversations expire after five minutes.
- Long lists: projects page 40 at a time; the assignee chooser shows the first 25 people
  (type an email for others); project and Due soon lists show the newest 200 tasks with a hint.

Normal text/voice messages outside these conversations still create personal tasks. Voice
recordings first return their transcript for **Cancel** / **Approve**, including task titles,
descriptions, comments, and task-field edits. Voice project tasks retain their selected project,
assignee, and deadline through approval. Repeated calendar arrow clicks update the existing picker.

**Approve** uses the transcript as the answer to the current question. **Cancel** discards only
that transcript and asks the same question again, preserving the rest of the draft. Send another
voice note or type a replacement answer. Old buttons cannot approve or cancel a replacement
recording. Both buttons work directly in Telegram, including on a phone.

Tasks have a short **title** (up to 120 characters) and a separate complete **description**.
The website's Add task form collects both, and managers can edit them in task details.
In Telegram, **Add task** → choose Personal task or a project → type the title → send the
description as text or voice (or **Skip description**). Personal tasks then ask for a deadline;
project tasks first ask for an assignee, then a deadline. Choose a date and time and **Save deadline**,
or choose **No deadline**, to create the task. **Cancel** discards the draft. Voice
descriptions retain the entered title through transcription and retries (migration `0009`).
Quick text/voice messages outside Add task still receive a title from their message/transcript
without an LLM; the full original content remains the description. Cards show the headline above a description
preview, and search matches both fields. Telegram lists, details, and reminders show the title.
Migration `0008` fills titles for existing tasks while preserving their descriptions and status.

## Telegram login and buttons

1. Open your bot in a **private** chat and send `/start`.
2. Only **Log in** is available before authentication. Press it or send `/login`.
3. Enter the account email when prompted, then enter the password in a separate message.
4. The bot attempts to delete the password message immediately. Passwords are never tasks,
   stored conversation state, or application log contents. During the password step, text
   starting with `/` is treated as the password (except `/cancel`). If the password arrives
   after the five-minute login step expired, the bot still deletes that first message and
   asks you to `/login` again.
5. After successful login, the bot shows **My tasks**, **Add task**, **Log out**,
   **Help**, **Projects**, and **Due soon** both inside the chat and in a persistent keyboard
   below the typing area.
   The right column is **Due soon**, **My tasks**, **Projects** from top to bottom; the left
   column is **Add task**, **Help**, **Log out**.
   Both menus use the same six-button order. The bottom keyboard also has a full-width
   **🏠 Menu** button in its own final row. Sign-in, Help, `/start`, `/menu`, and the chat's
   **🏠 Menu** button refresh both menus; dashboard links retain the bottom keyboard.
   Login prompts and confirmed logout remove the authenticated keyboard.

Before login, text/voice task creation, task browsing, dashboard-link generation, retries,
logout actions, and old inline buttons are blocked. The default Telegram command menu
contains `/start` and `/login`; a logged-in chat receives the full command menu.

After login:

- Send one text message to create one task with its complete text.
- Send a voice note up to five minutes and 20 MB. The bot acknowledges it, queues local
  transcription, and returns the text with **Cancel** / **Approve**. The task is created only after approval.
- **My tasks** shows the account's tasks five at a time with Previous/Next and Refresh.
  Each task is one compact numbered headline with its status emoji; long headlines end in “…”.
  Descriptions and other metadata appear after opening a task, keeping long tasks from
  expanding the list. Project and Due soon lists use the same compact previews.
  Tap its emoji task button for details, source, creation time, and recent comments. Rich
  messages use escaped HTML, readable dates in `APP_TIMEZONE`, and due-soon/overdue markers.
- **Add task** replaces the logged-in **Log in / Dashboard** button. Choose a project you
  manage, send task text or voice, choose an assignee, then select the deadline in the calendar.
  **Personal task** creates a task for your own My tasks view. **Cancel** or `/cancel` exits
  the chooser. `/newtask` opens it directly. Send `/menu` to refresh an older menu message.
- `/dashboard` issues a private browser sign-in link for the already authenticated account.
  Open local links on the computer running Docker.
- **Log out** offers two choices. **Log out of Telegram only** ends this bot login and keeps
  website sessions active. **Log out of all website sessions** also revokes the account's
  website sessions and outstanding website sign-in links. Both clear recent chat messages;
  one fresh login prompt remains and only Log in is available afterward.
- `/help`, `/tasks`, `/newtask`, `/dashboard`, `/logout`, and `/menu` are available after login.

The credential conversation expires after five minutes. `/cancel` exits it. Telegram and
website sessions expire after seven days. Website logout offers this-device and all-device
options; confirmed Telegram logout also revokes the account's browser sessions.

Logging out and logging into the other test account in the same Telegram chat switches the
account context. Tasks already created stay with their original account. Older Telegram-only
records remain in the database; when their Telegram owner first completes password login,
that owner's old tasks/jobs are attached to the selected account.

Run one polling process per bot token. Remove a pre-existing webhook and stop other pollers
before using this local long-polling bot.

## Data flow and reliability

```text
Email/password ──> Web session / Telegram account session
                                │
Telegram text/voice ──> Bot ──> PostgreSQL task/job + durable outbox
                                                 │
                                          Outbox dispatcher
                                           │             │
                                      Redis queue    Redis Pub/Sub
                                           │             │
                             Local Whisper worker        API ──SSE──> React dashboard
                                           │                          │
                                      PostgreSQL <────── API <── create / status / delete
                                           │
                                    Notification outbox ──> separate worker ──> Telegram
```

- **Accounts:** Email/password authentication uses Argon2id with 19 MiB memory, two iterations,
  and one lane. Telegram IDs identify the chat, while its authenticated account owns tasks.
  Requests are scoped to that account; another account's task IDs return 404.
- **Login throttling:** Redis atomically limits login attempts to 10 per email and 30 per
  source per minute. Nginx overwrites `X-Forwarded-For` with the connecting client's address
  and uvicorn trusts it (the API port is reachable only inside the Compose network), so the
  source limit is per browser client rather than shared by everyone. Counter keys hash their
  identifiers. Authentication fails closed if its storage is unavailable. Incorrect
  account/password responses are deliberately identical.
- **Web sessions:** Random session secrets are stored only as hashes. Cookies are HttpOnly
  and SameSite=Lax. Mutations require the configured Origin. Session issuance and global
  revocation lock the account row to avoid racing an old sign-in link against logout.
  Signing in again from a browser revokes the session that browser previously held.
- **Telegram access:** An outer middleware checks every private message and callback before
  handlers. Redis stores only the email and current login step for up to five minutes, a
  15-minute "password requested" flag (never the password), and, during an account-switch
  confirmation, the pending sign-in link for up to five minutes.
  PostgreSQL stores expiring Telegram-to-account sessions. Domain methods independently
  require an authenticated binding, preventing handlers from auto-creating anonymous users.
- **Publishing:** Task/job changes and their outbox entries commit atomically. The dispatcher
  publishes pending entries every 250 ms and retries after outages. Delivery is at least once.
  Each entry is published in its own savepoint; an entry that keeps failing is set aside after
  five attempts (only the error type is recorded), and broker outages don't count against entries.
- **Logout chat cleanup:** Migration `0005` tracks only private-chat message IDs/timestamps,
  including incoming messages, bot replies, confirmations, assignments, and reminders. Logout
  and cleanup requests commit together; the notification worker deletes in batches of up to
  100, resumes partial progress, skips undeletable messages, and retries transient failures.
  New conversations and saved tasks survive. Cancel never schedules cleanup. On the first
  cleanup, a bounded scan of the preceding 1,000 message IDs also attempts to remove menus
  from before tracking was installed. Telegram permits deletion only within 48 hours; older
  or unidentifiable history needs Telegram's **Clear History** action. See the
  [Telegram deletion limits](https://core.telegram.org/bots/api#deletemessage).
- **Duplicates:** A Telegram chat/message pair is unique for tasks and voice jobs. Advisory
  locks, retry generations, and atomic worker completion prevent duplicate voice tasks.
  Telegram notifications can repeat after a crash between sending and acknowledgment.
- **Task creation/deletion:** Website creation accepts 1–10,000 characters, rejects blank text,
  and preserves the submitted content. Deletion is account-scoped and marks the record deleted;
  both API and bot lists exclude it. Its Telegram message key remains reserved so a redelivery
  cannot recreate the task. Creation, deletion, and status changes all emit committed live events.
- **Voice:** Both metadata and streamed bytes are limited to 20 MB. Audio decoding stops at
  five minutes. Corrupt audio, silence, and empty transcripts receive clear failures. There
  are three retries after the initial attempt, backed by a persisted four-attempt budget.
  Each attempt has a 570-second soft and 600-second hard timeout; a timeout fails at once
  rather than retrying. Invalid audio and timeouts fail without a Retry button. Temporary
  audio is removed on completion/failure, and stale `/tmp/taskbot-*` directories left by a
  killed worker are swept when the worker starts. The model is cached and loaded once per
  worker process; the bot container does not load it.
  Every recording (new tasks, comments, wizard titles, and title/description edits) is saved
  as a durable job first, then acknowledged ("🎙 Got it — transcribing…"), and transcribed by
  the dedicated Celery worker through the Redis queue, so one recording never delays other
  bot users. While it works, the chat shows a typing indicator. New recordings return the complete
  transcript (split into messages when needed) with exactly **Cancel** and **Approve** on the final
  part. Only approval advances a wizard, creates a task/comment, or changes a field. Cancel discards
  the transcript while keeping the question open for another recording or typed answer. A typed answer sent
  before transcription finishes supersedes the recording. Permissions are checked again when
  the approved text is applied. Older queued task/comment/edit jobs retain their original behavior.
- **Recovery:** Late acknowledgments and Redis's 15-minute visibility timeout recover work
  after interruption. The dispatcher requeues jobs stuck in processing after 11 minutes and
  jobs still queued after 60 minutes, without resetting their attempt budget. A duplicate
  delivery that finds the job busy retries later. Manual retries are account-scoped.
- **Live updates:** Committed changes publish account-scoped task IDs. The dashboard refetches
  on SSE events/reconnect and every 30 seconds for reconciliation. If the stream fails (for
  example a 502 while the API container restarts) the browser reopens it with exponential
  backoff up to 30 seconds. Expired/revoked browser sessions close event streams within five
  seconds and return the browser to the login page. Each account may hold 20 open streams.
  Nginx disables SSE buffering and refreshes Docker DNS after API container replacement.
- **Task details:** Saving sends only the fields you changed, and a notice appears if the
  task was updated elsewhere while the dialog was open.
- **Notifications:** Separate queue and worker; a failed confirmation does not rerun successful
  transcription. Transient notification errors retry three times before logging exhaustion,
  always waiting at least as long as Telegram's 429 `retry_after`.

Migration `0002` keeps tasks intact and invalidates older Telegram-only web sessions and
sign-in links once, so existing users must complete the new account login. Downgrading this
migration requires an explicit mapping for email-only accounts, preserving their data.
Migration `0003` adds dashboard sources and task deletion without changing existing tasks.
Migration `0015` adds voice-job kinds for wizard titles and edits, outbox attempt tracking,
reminder retry bookkeeping, and indexes for recovery and cleanup lookups. Downgrades refuse
to run when they would lose data.

## API

| Method and path | Behavior |
|---|---|
| `POST /api/auth/login` | `{ "email": "...", "password": "..." }`; issue browser session |
| `POST /api/auth/exchange` | Consume a one-time link issued by an authenticated Telegram user |
| `POST /api/auth/telegram-link` | Issue a single-use bot sign-in link for the authenticated browser |
| `GET /api/me` | Current `{ id, name, email, role }` |
| `POST /api/auth/logout` | Revoke current browser session |
| `POST /api/auth/logout-all` | Revoke the account's browser/Telegram sessions and outstanding sign-in links |
| `GET /api/config` | Public bot URL; no credentials |
| `GET /api/voice-review` | Legacy draft editor for existing short-lived `X-Voice-Edit-Token` links; new reviews use Telegram buttons |
| `POST /api/voice-review` | Legacy typed `{ text, revision }` correction; sends a fresh Telegram review without applying it |
| `GET /api/tasks` | Account's tasks, newest first; `limit` (1–1000, default 1000) and `offset` page the list. The dashboard fetches pages of 500 |
| `GET /api/tasks?mine=true` | Personal and assigned tasks |
| `GET /api/tasks?project_id={uuid}` | Visible project board |
| `GET /api/tasks?due_soon=true` | Visible incomplete tasks due within 96 hours or overdue; combine with `mine=true` |
| `POST /api/tasks` | Create `{ "text": "...", "status": "pending" }`; status optional; returns 201 |
| `PATCH /api/tasks/{uuid}` | Status changes; managers may also set `title`, `text`, `project_id`, `assignee_ids`, and `due_at`; `{ "archived": true/false }` archives or restores a completed task. Moving a task out of its project (`project_id: null`) makes the mover its owner |
| `DELETE /api/tasks/{uuid}` | Delete the account's task from both views; returns 204 |
| `GET /api/projects`, `POST /api/projects` | List active visible projects / create a named project; `?archived=true` lists archived projects |
| `PATCH /api/projects/{uuid}` | Manager/admin sets `{ "archived": true }` or `{ "archived": false }` to archive or restore a project |
| `GET /api/projects/{uuid}/members`, `POST /api/projects/{uuid}/members` | Read people / add an existing account using `{ "email": "..." }` |
| `DELETE /api/projects/{uuid}/members/{user_uuid}` | Manager removes a member (not the owner); returns 204 |
| `GET /api/users` | Manager/admin account directory for assignment |
| `GET /api/tasks/{uuid}` | Read task details and current permissions |
| `GET /api/tasks/{uuid}/comments`, `POST /api/tasks/{uuid}/comments` | Read discussion / post `{ "text": "..." }` |
| `GET /api/events` | Authenticated, account-scoped SSE |
| `GET /health/live` | API liveness |
| `GET /health/ready` | PostgreSQL and Redis readiness |

Tasks expose UUIDs, full text, source (`text`, `voice`, or `dashboard`), status, and UTC creation/update timestamps. Errors use
`{ "error": { "message": "..." } }`. Missing sessions return 401, forbidden origins 403,
foreign tasks 404, invalid input 422, and throttled login 429. Text containing NUL characters,
out-of-range paging, and deadlines outside 1970–2100 are rejected with 422.
Task creation and edits accept `assignee_ids` (an array of project member UUIDs); an empty
array leaves a project task unassigned. Task creation also accepts optional `project_id` and
an ISO 8601 `due_at` with timezone. Responses include `assignee_ids`, `assignees`, project
details, deadline, `can_manage`, and `can_move`. The legacy `assignee_id`/`assignee` fields
represent the first assignee; clients submitting `assignee_id` replace the selection with
that one person. Migration `0013` preserves existing assignments and adds support for more people.

## Configuration and operations

`.env.example` lists configuration. Set `TELEGRAM_BOT_TOKEN` and keep `.env` private and
excluded from Git. `APP_ORIGIN` must exactly match the browser origin; changing `APP_PORT`
also requires updating it. Local defaults are `http://localhost:8080` and insecure cookies
for HTTP loopback; HTTPS hosting uses `COOKIE_SECURE=true`.

`WHISPER_MODEL` defaults to `small`, `WHISPER_CPU_THREADS` to 4, and `LOG_LEVEL` to INFO.
`WHISPER_BEAM_SIZE` defaults to 1 (greedy decoding): on the bundled speech sample it gave the
same transcript as beam search and transcribed a one-minute note in about 4.6 s instead of
12.2 s on 4 CPU threads. Raise it to trade speed for accuracy. The transcription worker loads
and warms the model when it starts, so the first voice note after a restart isn't slowed.
Python development also accepts `DATABASE_URL`, `REDIS_URL`, and `WHISPER_CACHE`; Compose
supplies them automatically. Use URL-safe database credentials in Compose connection URLs.

```bash
docker compose ps -a
docker compose logs --tail=80 api bot dispatcher transcription-worker notification-worker
curl http://localhost:8080/health/ready
docker compose down    # stop, preserve data
docker compose up -d   # restart with existing data
```

PostgreSQL, Redis queue data, and model weights use named volumes. Workers have Celery
heartbeat/ping checks; bot/dispatcher heartbeats expire after 30 seconds. Application logs
report identifiers, attempts, duration, and exception classes without task/password contents
or credential-bearing Telegram request URLs. Database, Redis, and Nginx use native logs.

Common issues: invalid token or another poller prevents bot startup; Hugging Face access,
disk space, or model-download errors delay voice work; wrong `APP_ORIGIN` causes 403s;
10 failed email login attempts require waiting a minute. PostgreSQL initialization variables
do not update passwords in an existing volume. An unavailable Redis disables authentication
and delays background work; accepted outbox entries persist in PostgreSQL.

To deliberately erase local tasks, accounts, sessions, and cached models, `docker compose down -v`
removes the application's persistent volumes. Normal shutdown does not require it.

## Verification

The backend suite uses real PostgreSQL/Redis and mocked Telegram transport. It refuses to
reset anything without a `*_test` database, Redis database 15, and explicit reset opt-in.

```bash
bash scripts/test.sh                         # isolated standard backend suite
bash scripts/test.sh pytest -m transcription # real local Whisper queue test
bash scripts/check-platforms.sh              # ARM64/AMD64 CPU image packaging checks
```

For development with Python 3.12/uv and Node.js 22:

```bash
cd backend
uv sync --frozen --extra transcription
uv run ruff check app migrations tests
uv run ruff format --check app migrations tests
ALLOW_TEST_DATABASE_RESET=1 uv run pytest -m 'not transcription'
cd ../frontend
npm ci
npm run lint
npm run build
npx playwright install chromium
npm test
```

Tests cover both accounts, incorrect passwords, throttling, origin checks, cross-account
isolation, Telegram credential entry, password message removal, blocked commands/old buttons
before login, account switching, login and logout popup, device-scoped logout, direct dashboard access, Kanban statuses,
optimistic rollback, SSE/reconnect, voice failures, and worker recovery. Telegram transport
is mocked in automated tests; live credentials are read only from the local `.env` file.
