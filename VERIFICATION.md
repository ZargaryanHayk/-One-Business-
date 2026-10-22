# Verification record

Verified locally on 2026-10-20/21 using macOS ARM64, Python 3.12, Node.js, and Docker Desktop.

| Check | Result |
|---|---|
| Python lint and formatting | Passed |
| Frontend ESLint and TypeScript / production build | Passed |
| Backend suite with real PostgreSQL and Redis | 189 passed; two separately exercised transcription integration tests deselected |
| Real Whisper → Celery → PostgreSQL queue test | 2 passed: new voice task and voice comment on an existing task, using isolated PostgreSQL/Redis and mocked Telegram download/send transport |
| Chromium desktop and mobile browser suite | 71 passed; desktop copy of mobile-only touch test intentionally skipped. Covers separate task titles/descriptions, autofill login, logout popup and device choices, projects, assignments, comments, Due soon, drag/drop, authentication, and mobile layout |
| Credential migration in running PostgreSQL | Revision `0002` applied; both seeded accounts verified against their Argon2id hashes |
| Corrected developer credential | Revision `0006` applied; test2@hire.me now uses hireme2, and its old password is rejected. First account, account identities, task/project/comment counts, and browser/Telegram session counts preserved. Full backend suite: 152 passed, one transcription test deselected; eight desktop/mobile login checks passed. Fresh browser login against the running website returned 200, opened the dashboard, and retained authentication; only the verification session was removed directly |
| Task-management migration in running PostgreSQL | Revision `0003` applied; schema drift check passed |
| Project-workspace migration in running PostgreSQL | Revision `0004` applied; seeded manager/developer roles and schema drift check verified |
| Chat-cleanup migration in running PostgreSQL | Revision `0005` applied; schema drift check passed in the isolated test database; deployed cleanup and duplicate delivery checked with mocked Telegram calls and temporary records removed |
| Running HTTP API credential checks | Both accounts log in, read only their tasks, and lose access after logout |
| Running website with actual API | Both seeded accounts complete browser login, dashboard access, logout confirmation, and subsequent API rejection; login/logout screenshots visually inspected |
| Telegram authentication and menu regression checks | Logged-out commands, text/voice tasks, and stale callbacks blocked; login unlocks six menu buttons; logout locks them again |
| Menus inside chat and below typing area | Restored the persistent reply keyboard alongside the inline chat menu. Both use one shared six-button layout: Add task/Due soon, Help/My tasks, Log out/Projects. Sign-in and menu/help entrypoints show both, and dashboard links retain the bottom keyboard. Python lint, ten transport-free menu checks, and the full backend suite passed. Deployed bot rendered both keyboards with mocked transport; no real messages sent. The earlier usage-limit block cleared after its retry time, and deployment proceeded |
| Full-width bottom Menu button | Bottom reply keyboard has rows of 2, 2, 2, and 1 buttons, with 🏠 Menu alone in the final row. Existing Menu handler refreshes both menus. Ten menu checks and lint passed; bot rebuilt/restarted healthy, and deployed keyboard structure verified without sending Telegram messages |
| Compact Telegram task lists | My tasks, project, and Due soon lists use one numbered status/headline line per task. Whitespace is collapsed; previews are limited to 45 UTF-16 units with an ellipsis. Descriptions and metadata remain in task details. Long multiline content, legacy text fallback, HTML escaping, pagination, and menu/task controls passed 81 targeted checks. Bot rebuilt/restarted; no test messages sent to Telegram |
| Telegram Add task menu | Add task replaces Log in / Dashboard in logged-in inline/reply menus and task lists. Button and /newtask open project selection; selection precedes task input, project tasks continue through assignee and deadline calendar, personal text/voice creation and cancellation pass. /menu refreshes the persistent keyboard; /dashboard remains available as a command |
| Telegram project conversations | Manager creates project, adds developer, assigns task/deadline; developer moves task and comments; stale manager actions rejected |
| Telegram task presentation | Lists and details separate task, project, status, assignee, deadline, source, and comments using emojis and bold labels. Task buttons replace raw UUID lines in lists. Escaping and 4,096-unit length checks cover untrusted markup, long emoji content, and comments; dates and overdue/completed states checked |
| Telegram voice comments | Comment conversation accepts voice notes, validates duration/size, and durably records the selected task. Worker saves transcript, job completion, notification, and task events together. Checked duplicate updates/jobs, owner-only retry, removed project access, deleted targets, silence/corrupt audio, comment length, and notification failure without retranscription |
| Deployed voice comments | Revision `0007` applied, Alembic schema drift check passed, and deployed bot routing verified. API, bot, dispatcher, and both workers healthy; real-model tests passed on isolated services, with no messages sent to real Telegram users |
| Separate task titles and descriptions | Migration `0008` backfilled seven existing titles and verified original descriptions, status, assignment, and timestamps unchanged. Titles are stored separately, limited to 120 characters, and editable by task managers. Text/voice tasks derive titles while keeping complete content. Telegram HTML escaping/length, title validation, cross-user access, and developer edit restrictions passed |
| Deployed title workflow | Actual API/browser creation, independent title edit, full description preservation, reload persistence, search by title/description, and second-tab SSE updates passed. Desktop/mobile screenshots visually checked in `output/dashboard/task-titles-desktop.png` and `task-titles-mobile.png`. Schema drift check passed; all services healthy. Temporary account/tasks/session removed; no real Telegram messages sent |
| Telegram deadline calendar | Inline month/day selection, leap day, time presets and minute changes, timezone conversion, save/clear/cancel, expired buttons, and cross-user isolation passed |
| Telegram calendar for voice tasks | Calendar confirmation uses the clicking person's identity and retains the voice recording's project, assignee, and selected deadline |
| Deployed Telegram calendar | Running bot code displayed the inline month grid and saved a leap-day deadline with selected local time in PostgreSQL; mocked outgoing transport, temporary task/account removed |
| Reminder outbox → Redis → worker | Passed with mocked Telegram transport; one reminder scheduled per task/assignee/deadline, duplicate sends suppressed by persisted sent state |
| Reminder cancellation and recovery | Completed, deleted, rescheduled, reassigned, and logged-out cases suppressed; transient failure can retry successfully |
| Backend, transcription, and Nginx/frontend Docker builds | Passed |
| Linux ARM64 and AMD64 CPU dependency/API import checks | Passed |
| Actual local Whisper recognition inside Linux ARM64 container | Passed |
| Migrations and API/bot/dispatcher/both-worker health checks | Passed after credential deployment |
| Alembic schema drift check | No upgrade operations detected |
| Task status and session persistence across API/PostgreSQL restart | Passed; temporary QA data removed |
| Running React dashboard with actual API/PostgreSQL/Redis/SSE | Passed: simulated bot-handler input appeared without a page refresh; all statuses persisted after reload |
| Running Kanban creation and deletion | Website-created task appeared in a second browser tab through real SSE; all three task sources deleted from both tabs and remained absent after reload |
| Running Kanban drag and drop | Dragged a card through all three columns against the actual API; persisted across reload and appeared in a second tab through SSE; drag preview visually inspected |
| Running shared project workflow | Manager created project, added developer, assigned deadlines; developer's project sidebar and My Tasks updated live; comments appeared across accounts through SSE |
| Running Due soon and role enforcement | Only incomplete tasks within 96 hours/overdue shown; completion removed task and persisted after reload; developer project creation returned 403 |
| Running dashboard responsive layout | Passed at 1440px desktop and 390px mobile; previews in `output/dashboard/` |
| Sidebar clipping and navigation appearance | Inactive buttons transparent, selected project highlighted, Telegram link kept on one line. Checked at 1100×600, 1440×1000, 1100×450, and 390×844; optional panel hidden in short windows and fully visible on tall desktop. Preview: `output/dashboard/sidebar-fixed.png` |
| Task-card project labels | Project name replaces the source badge; personal tasks show Personal. Long names truncate with an ellipsis and retain the full hover title. Deployed CSS checked for all three task sources at 1440px, 1100px, and 390px without overflow; preview uses mocked records in `output/dashboard/project-badges.png` |
| Dashboard source filter removed | Source dropdown and its filtering state/styles removed; task search still finds matching tasks and shows empty search results. Full browser suite: 67 passed, one mobile-only test skipped on desktop; lint and production build passed |
| Device-scoped logout | One-device logout retains other browser and Telegram sessions. All-device logout revokes only the current account's browser/Telegram sessions and sign-in links, retains tasks, rejects foreign origins and unauthenticated requests, and preserves other accounts |
| Logout chat cleanup | Both confirmed website choices and Telegram logout queue cleanup; Cancel does not. Tests cover bounded legacy history scan, 100-message batches, undeletable messages, retries, duplicate jobs, preserving new conversations, clearing stale FSM/keyboard, notification suppression, and outbox → Redis → Celery delivery with Telegram mocked |
| Simplified login page | Removed the two descriptive sentences, numbered Telegram instructions, check-again link, and privacy note; login/logout and full desktop/mobile browser suite passed. Deployed page checked at 1440px and 390px; previews: `output/dashboard/login-clean.png` and `login-clean-mobile.png` |
| Autofill login regression | Reproduced a form displaying corrected autofill credentials while submitting earlier React state. Login now reads native form fields at submission and clears the password on completion/failure. Regression passes on desktop/mobile. An existing keyboard-drag check was intermittent in the initial run; the complete rerun passed |
| Deployed autofill login | Replaced earlier field values without React input events, then signed in successfully as test2@hire.me through the actual API, reached the dashboard, and verified the session. Only the verification session was revoked directly; Telegram chat cleanup was not triggered |
| Deployed logout popup | Checked on desktop/mobile with temporary accounts: cancel retains the board, one-device logout keeps other sessions active, all-device logout signs out another browser through SSE, and another account stays signed in. Tasks survive logout. QA users/tasks/sessions removed; screenshots: `output/dashboard/logout-popup.png` and `logout-popup-mobile.png` |

The speech fixture was recognized as “Remember to review the project plan tomorrow.”
The browser screenshots were visually inspected at desktop and mobile widths.

The 2026-10-21 project-workspace walkthrough used temporary manager/developer accounts with
no Telegram bindings, so no test notifications were sent to real users. Screenshots in
`output/dashboard/project-manager.png`, `developer-my-tasks.png`, `project-comments.png`,
`due-soon.png`, and `project-mobile.png` contain only these QA records. The accounts, projects,
tasks, comments, sessions, and events were removed after verification. Application services
remain healthy; the isolated test database and Redis were stopped.

The seeded accounts are `test1@hire.me` (project manager) and `test2@hire.me` (developer).
Project authorization is shared by HTTP and Telegram. Members can read and comment on their
project boards; only managers can edit assignments/deadlines/delete, while assignees can move
their tasks. Personal tasks remain private. Reminder checks include overdue tasks and exclude
completed/deleted tasks; delivery requires an active Telegram login and rechecks current task
state and recipient before sending. The reminder integration exercises the actual durable
outbox, Redis broker, and Celery worker with Telegram HTTP transport mocked.

The 2026-10-21 task-management checks cover web creation in every status, default Pending,
blank/oversized validation, ownership and request-origin checks, deletion of all three task
sources, account-scoped Redis events, and deleted tasks staying absent after Telegram text
redelivery or voice job redelivery. Deletion removes tasks from both web and Telegram lists.
The updated website was visually inspected at 1440px and 390px. Preview images are
`output/dashboard/kanban-desktop.png`, `kanban-mobile.png`, and `create-task.png`. These
contain only temporary QA records; that account, its tasks, sessions, and events were removed.

Drag-and-drop checks exercise all six status transitions, empty columns, whole-card mouse
dragging, mobile touch gestures, keyboard pickup/arrow/drop, Escape cancellation, outside-board
and same-column drops, reload persistence, and failed-save rollback. Selectors and drag/drop
share the same optimistic mutation and error handling. Frontend lint, TypeScript, production
build, and the full browser suite pass after adding these controls.
The deployed frontend was checked with an isolated account, then that account and its QA
records were removed. `output/dashboard/kanban-dragging.png` shows the floating card and
highlighted destination using only temporary test data.

The credential migrations seed `test1@hire.me` and `test2@hire.me` with the requested test
passwords. API verification used the actual running PostgreSQL/Redis services. Telegram
credential-flow verification used the actual bot handlers with mocked outgoing transport;
passwords never became task records. The browser suite covers both account forms on desktop
and mobile, wrong passwords, explicit logout confirmation, and protected routes.

The running dashboard was additionally checked using a temporary isolated account. Three
tasks were submitted through the actual text-message handler, with outgoing Telegram
notifications suppressed. The first appeared via the real outbox/Redis/SSE path in an observed
388 ms after the handler returned, without refreshing the page. Pending, In Progress, and
Completed were changed through the browser and checked against persisted API data. The
temporary account, sessions, and tasks were removed afterward. Preview images contain only
this temporary test data.

The automated Telegram transport is mocked. After credentials were supplied, Telegram's
`getMe` verified `@Task_HRM_bot`, long polling started successfully, and all application
services passed their health checks. The token is stored only in the Git-ignored `.env`
file with owner-only permissions (0600).

A live text/voice walkthrough still requires sending messages to the bot from a Telegram
account. Automated checks did not send messages to real Telegram users.

Run the reproducible commands in `README.md` to repeat these checks. Platform checks verify
image packaging and imports on both architectures; full backend and UI tests were not run
natively on a separate AMD64 machine.


## Website-to-Telegram auto-login · 2026-10-21

- 94 authentication and bot tests passed, including the new single-use link flow,
  concurrent redemption, account switching, expiry, browser/Telegram logout revocation,
  origin checks, and handling links during an unfinished password conversation.
- Six desktop/mobile browser checks passed for the Open Telegram control, fresh-link
  navigation, error display, closed failed tabs, and retry.
- Frontend TypeScript/production build and ESLint passed. Ruff lint and format checks
  passed for all seven changed/new Python files. The repository-wide format check
  also reported an existing trailing blank line in `app/transcription.py`.
- Telegram transport was mocked; these checks did not send messages to real users.
- Applied migration `0012` to the local app and rebuilt/restarted API, bot,
  dispatcher, and frontend. All four passed startup health checks. Live read-only
  checks confirmed the migration/table, the served frontend's new login-link request,
  an HTTP 401 for unauthenticated link issuance, and HTTP 200 readiness.

## 2026-10-22 review, stress test, and fixes

A code review plus a live stress/fuzz run found and fixed these defects:

| Defect (before) | Fix | Verified |
|---|---|---|
| A deadline such as `0001-01-01T00:00:00+14:00` was accepted, stored as a BC timestamp, and made every list/detail/comment request for that user return 500 | Deadlines must fall between 1970 and 2100 (UTC) on web and Telegram | Live: 422; board still loads |
| Login throttle keyed on nginx's container IP, so 30 failures from anyone locked out every web user | Nginx overwrites `X-Forwarded-For`; uvicorn trusts it | Live: lockout limited to the attacking client; spoofed header doesn't bypass; another client signs in |
| NUL characters and negative/huge `limit`/`offset` returned 500 | Rejected with 422 | Live fuzz: all 422 |
| Task list silently truncated at 200 (deployed) / 2000 (source) | Bounded paging; dashboard fetches pages of 500 | Live: all 309 tasks returned |
| Deployed containers differed from source | Rebuilt and redeployed | All services' code hashes match source; migration `0015` applied |
| Wizard voice (titles/edits) transcribed inside the bot, freezing it for all users | Routed through the durable job → Redis → worker queue; bot no longer loads Whisper | Tests with real Redis FSM |
| Shared sign-in links silently switched accounts (web and Telegram) and could move legacy data | Confirmation before switching; link sign-in never migrates legacy data | Backend and browser tests |
| Inline navigation kept the old conversation, so typed text went to the wrong task | Navigation ends the conversation | Bot tests |
| SSE never reconnected after an HTTP error; 401 mid-session didn't log out; failed status change could keep the wrong column | Backoff reconnect, global 401 handler, snapshot rollback | Browser tests |
| Voice pipeline: soft timeouts retried, stuck jobs waited 20 min, one bad outbox row stalled delivery, unbounded reminder scan, acknowledgment before save, Retry on permanent errors | See README "Data flow and reliability" | Backend tests |

| Check | Result |
|---|---|
| Backend suite (real PostgreSQL/Redis) | 332 passed; 2 real-Whisper tests run separately |
| Real Whisper → Celery → PostgreSQL | 2 passed |
| Chromium desktop and mobile browser suite | 131 passed, 3 skipped |
| Ruff check/format, ESLint, TypeScript build, `alembic check` | Passed; no schema drift |
| Live stress: 300 concurrent creates, 90 concurrent status changes, 60 SSE streams, fuzzing | 36/36 checks passed; p95 create latency about 400 ms |

Stress data was tagged and permanently removed afterwards. The earlier authorization probe sent
one real assignment notification to the developer test account; the post-fix run sent none.
