import { test, expect, type Page } from "@playwright/test";
import {
  taskAssigneeIds,
  type Project,
  type Task,
  type TaskComment,
  type User,
} from "../src/api";

declare global {
  interface Window {
    taskbotTestStream: EventTarget & { fail(): void };
    taskbotTestStreams: number;
  }
}

const initial: Task[] = [
  {
    id: "1",
    title: "Review the launch proposal",
    text: "Review the launch proposal",
    source: "text",
    status: "pending",
    created_at: "2026-10-20T10:00:00Z",
    updated_at: "2026-10-20T10:00:00Z",
  },
  {
    id: "2",
    title: "Bring the new onboarding flow to life",
    text: "Bring the new onboarding flow to life",
    source: "voice",
    status: "in_progress",
    created_at: "2026-10-20T09:00:00Z",
    updated_at: "2026-10-20T09:00:00Z",
  },
  {
    id: "3",
    title: "Send the weekly team update",
    text: "Send the weekly team update",
    source: "text",
    status: "completed",
    created_at: "2026-10-20T08:00:00Z",
    updated_at: "2026-10-20T08:00:00Z",
  },
];

async function mockApi(
  page: Page,
  options: {
    signedIn?: boolean;
    empty?: boolean;
    taskFailure?: boolean;
    role?: User["role"];
  } = {},
) {
  const state = {
    tasks: options.empty ? ([] as Task[]) : structuredClone(initial),
    failList: false,
    taskRequests: [] as string[],
    patches: [] as Record<string, unknown>[],
    failUpdate: false,
    failCreate: false,
    failDelete: false,
    failArchive: false,
    failProjectArchive: false,
    statusRequests: 0,
    signedIn: options.signedIn ?? true,
    exchanged: "",
    logoutPath: "",
    failLogout: false,
    telegramLinks: 0,
    failTelegramLink: false,
    account: {
      id: "user-1",
      name: "Alex Morgan",
      email: "test1@hire.me",
      role: options.role ?? "developer",
    } as User,
    projects: [] as Project[],
    members: {} as Record<string, User[]>,
    comments: {} as Record<string, TaskComment[]>,
    accounts: [
      {
        id: "user-1",
        name: "Alex Morgan",
        email: "test1@hire.me",
        role: "project_manager",
      },
      {
        id: "user-2",
        name: "Jamie Developer",
        email: "test2@hire.me",
        role: "developer",
      },
    ] as User[],
  };
  await page.addInitScript(() => {
    class TestStream extends EventTarget {
      static CONNECTING = 0;
      static OPEN = 1;
      static CLOSED = 2;
      readyState = 0;
      onerror: ((event: Event) => void) | null = null;
      constructor() {
        super();
        window.taskbotTestStream = this;
        window.taskbotTestStreams = (window.taskbotTestStreams ?? 0) + 1;
        setTimeout(() => {
          if (this.readyState === 2) return;
          this.readyState = 1;
          this.dispatchEvent(new MessageEvent("ready"));
        }, 30);
      }
      // Simulate an HTTP error response (e.g. 502), which closes EventSource.
      fail() {
        this.readyState = 2;
        this.onerror?.(new Event("error"));
      }
      close() {
        this.readyState = 2;
      }
    }
    Object.defineProperty(window, "EventSource", { value: TestStream });
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (value: unknown, status = 200) =>
      route.fulfill({ status, json: value });
    if (path === "/api/config")
      return json({ telegram_bot_url: "https://t.me/taskline_test_bot" });
    if (path === "/api/auth/telegram-link") {
      expect(route.request().method()).toBe("POST");
      state.telegramLinks++;
      if (state.failTelegramLink)
        return json(
          { error: { message: "Telegram is temporarily unavailable." } },
          503,
        );
      return json({
        url: "https://t.me/taskline_test_bot?start=web_test_token",
      });
    }
    if (path === "/api/auth/exchange") {
      state.exchanged = route.request().postDataJSON().token;
      state.signedIn = true;
      return json({ ok: true });
    }
    if (path === "/api/auth/login") {
      const { email, password } = route.request().postDataJSON();
      const valid =
        (email === "test1@hire.me" && password === "hireme1") ||
        (email === "test2@hire.me" && password === "hireme2");
      if (!valid)
        return json(
          { error: { message: "Email or password is incorrect" } },
          401,
        );
      state.signedIn = true;
      state.account = {
        id: email === "test1@hire.me" ? "user-1" : "user-2",
        name: email === "test1@hire.me" ? "Test One" : "Test Two",
        email,
        role: email === "test1@hire.me" ? "project_manager" : "developer",
      };
      return json(state.account);
    }
    if (path === "/api/me")
      return state.signedIn
        ? json(state.account)
        : json({ error: { message: "Sign in" } }, 401);
    if (
      !state.signedIn &&
      path !== "/api/auth/exchange" &&
      path !== "/api/auth/login"
    )
      return json({ error: { message: "Sign in" } }, 401);
    if (path === "/api/auth/logout" || path === "/api/auth/logout-all") {
      state.logoutPath = path;
      if (state.failLogout)
        return json(
          { error: { message: "Could not log out. Try again." } },
          503,
        );
      state.signedIn = false;
      return json({ ok: true });
    }
    if (path === "/api/users") return json(state.accounts);
    if (path === "/api/projects" && route.request().method() === "POST") {
      const project: Project = {
        id: `project-${state.projects.length + 1}`,
        name: route.request().postDataJSON().name,
        owner_id: state.account.id,
        can_manage: true,
      };
      state.projects.push(project);
      state.members[project.id] = [state.account];
      return json(project, 201);
    }
    if (
      /^\/api\/projects\/[^/]+$/.test(path) &&
      route.request().method() === "PATCH"
    ) {
      if (state.failProjectArchive)
        return json(
          { error: { message: "Could not update project. Try again." } },
          503,
        );
      const project = state.projects.find(
        (project) => project.id === path.split("/").at(-1),
      )!;
      project.archived_at = route.request().postDataJSON().archived
        ? new Date().toISOString()
        : null;
      return json(project);
    }
    if (path === "/api/projects")
      return json(
        state.projects
          .filter((project) =>
            new URL(route.request().url()).searchParams.get("archived") ===
            "true"
              ? !!project.archived_at
              : !project.archived_at,
          )
          .filter(
            (project) =>
              state.account.role !== "developer" ||
              state.tasks.some(
                (task) =>
                  task.project_id === project.id &&
                  taskAssigneeIds(task).includes(state.account.id),
              ),
          )
          .map((project) => ({
            ...project,
            can_manage:
              state.account.role === "admin" ||
              (state.account.role === "project_manager" &&
                project.owner_id === state.account.id),
          })),
      );
    if (/\/api\/projects\/[^/]+\/members$/.test(path)) {
      const id = path.split("/")[3];
      if (route.request().method() === "POST") {
        const person = state.accounts.find(
          (person) => person.email === route.request().postDataJSON().email,
        )!;
        state.members[id] ??= [];
        state.members[id].push(person);
        return json(person);
      }
      return json(state.members[id] ?? []);
    }
    if (/\/api\/projects\/[^/]+\/members\/[^/]+$/.test(path)) {
      const [, , , projectId, , memberId] = path.split("/");
      if (route.request().method() === "DELETE") {
        for (const task of state.tasks) {
          if (task.project_id === projectId && task.assignee_id === memberId) {
            task.assignee_id = null;
            task.assignee = null;
          }
        }
        state.members[projectId] = (state.members[projectId] ?? []).filter(
          (m) => m.id !== memberId,
        );
        return route.fulfill({ status: 204 });
      }
    }
    if (/\/api\/tasks\/[^/]+\/comments$/.test(path)) {
      const id = path.split("/")[3];
      state.comments[id] ??= [];
      if (route.request().method() === "POST") {
        const comment = {
          id: `comment-${state.comments[id].length + 1}`,
          text: route.request().postDataJSON().text,
          author: state.account,
          created_at: new Date().toISOString(),
        };
        state.comments[id].push(comment);
        return json(comment, 201);
      }
      return json(state.comments[id]);
    }
    if (path === "/api/tasks" && route.request().method() === "POST") {
      if (state.failCreate)
        return json({ error: { message: "Could not create task." } }, 503);
      const task: Task = {
        ...initial[0],
        id: `web-${state.tasks.length + 1}`,
        ...route.request().postDataJSON(),
        source: "dashboard",
        user_id: state.account.id,
      };
      task.assignee_ids = task.assignee_ids?.length
        ? task.assignee_ids
        : task.project_id
          ? []
          : [state.account.id];
      task.assignee_id = task.assignee_ids[0] ?? null;
      task.assignees = state.accounts.filter((person) =>
        task.assignee_ids!.includes(person.id),
      );
      task.assignee =
        state.accounts.find((person) => person.id === task.assignee_id) ?? null;
      task.project_name =
        state.projects.find((project) => project.id === task.project_id)
          ?.name ?? null;
      state.tasks.unshift(task);
      return json(task, 201);
    }
    if (
      path.startsWith("/api/tasks/") &&
      route.request().method() === "DELETE"
    ) {
      if (state.failDelete)
        return json({ error: { message: "Could not delete task." } }, 503);
      state.tasks = state.tasks.filter(
        (task) => task.id !== path.split("/").at(-1),
      );
      return route.fulfill({ status: 204 });
    }
    if (path === "/api/tasks") {
      const url = new URL(route.request().url());
      state.taskRequests.push(url.search);
      const limit = Number(url.searchParams.get("limit") ?? 1000);
      const offset = Number(url.searchParams.get("offset") ?? 0);
      if (limit < 1 || limit > 1000 || offset < 0)
        return json({ error: { message: "Invalid paging" } }, 422);
      if (state.failList)
        return json({ error: { message: "Unavailable" } }, 503);
      const dueSoon = url.searchParams.get("due_soon") === "true";
      let tasksList = state.tasks.filter((task) =>
        url.searchParams.get("archived") === "true"
          ? !!task.archived_at
          : !task.archived_at,
      );
      if (url.searchParams.has("project_id")) {
        tasksList = tasksList.filter(
          (task) => task.project_id === url.searchParams.get("project_id"),
        );
      } else {
        tasksList = tasksList.filter(
          (task) =>
            !state.projects.find((project) => project.id === task.project_id)
              ?.archived_at,
        );
      }
      if (url.searchParams.get("mine") === "true") {
        tasksList = tasksList.filter((task) =>
          taskAssigneeIds(task).length
            ? taskAssigneeIds(task).includes(state.account.id)
            : !task.project_id &&
              (!task.user_id || task.user_id === state.account.id),
        );
      }
      if (dueSoon) {
        tasksList = tasksList.filter(
          (task) =>
            task.status !== "completed" &&
            task.due_at &&
            new Date(task.due_at).getTime() <= Date.now() + 4 * 86400000,
        );
      }
      return options.taskFailure
        ? json({ error: { message: "Unavailable" } }, 503)
        : json(
            tasksList.slice(offset, offset + limit).map((task) => ({
              ...task,
              project_archived: !!state.projects.find(
                (project) => project.id === task.project_id,
              )?.archived_at,
              can_manage:
                !state.projects.find(
                  (project) => project.id === task.project_id,
                )?.archived_at &&
                (!task.project_id ||
                  state.account.role === "admin" ||
                  (state.account.role === "project_manager" &&
                    state.projects.find(
                      (project) => project.id === task.project_id,
                    )?.owner_id === state.account.id)),
              can_archive:
                !state.projects.find(
                  (project) => project.id === task.project_id,
                )?.archived_at &&
                (!task.project_id ||
                  state.account.role === "admin" ||
                  taskAssigneeIds(task).includes(state.account.id) ||
                  (state.account.role === "project_manager" &&
                    state.projects.find(
                      (project) => project.id === task.project_id,
                    )?.owner_id === state.account.id)),
              can_move:
                !state.projects.find(
                  (project) => project.id === task.project_id,
                )?.archived_at &&
                (!task.project_id ||
                  taskAssigneeIds(task).includes(state.account.id) ||
                  state.account.role === "project_manager"),
            })),
          );
    }
    if (
      path.startsWith("/api/tasks/") &&
      route.request().method() === "PATCH"
    ) {
      const changes = route.request().postDataJSON();
      if ("archived" in changes) {
        if (state.failArchive)
          return json(
            { error: { message: "Could not archive task. Try again." } },
            503,
          );
        const task = state.tasks.find(
          (task) => task.id === path.split("/").at(-1),
        )!;
        task.archived_at = changes.archived ? new Date().toISOString() : null;
        return json(task);
      }
      state.statusRequests += 1;
      state.patches.push(changes);
      // Keep the optimistic move visible long enough to test pending state.
      await new Promise((resolve) => setTimeout(resolve, 150));
      if (state.failUpdate)
        return json({ error: { message: "Could not save status." } }, 503);
      const task = state.tasks.find((t) => t.id === path.split("/").at(-1))!;
      Object.assign(task, route.request().postDataJSON());
      task.updated_at = new Date().toISOString();
      if ("assignee_ids" in changes) {
        task.assignee_id = task.assignee_ids?.[0] ?? null;
        task.assignees = state.accounts.filter((person) =>
          task.assignee_ids!.includes(person.id),
        );
      }
      task.assignee =
        state.accounts.find((person) => person.id === task.assignee_id) ?? null;
      return json(task);
    }
    return json({ error: { message: "Not found" } }, 404);
  });
  return state;
}

test("renders the board and adapts without horizontal overflow", async ({
  page,
}, info) => {
  await mockApi(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /^My Tasks/ })).toBeVisible();
  await expect(page.getByTestId("task-card")).toHaveCount(3);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({ path: info.outputPath("board.png"), fullPage: true });
});

test("moves tasks through statuses and persists the change", async ({
  page,
}) => {
  const state = await mockApi(page);
  await page.goto("/");
  const card = page
    .getByTestId("task-card")
    .filter({ hasText: "Review the launch proposal" });
  await card.getByRole("combobox").selectOption("in_progress");
  await expect(
    page
      .getByRole("region", { name: "In progress", exact: true })
      .getByText("Review the launch proposal"),
  ).toBeVisible();
  await expect(card.getByRole("combobox")).toBeEnabled();
  await expect.poll(() => state.tasks[0].status).toBe("in_progress");
  await card.getByRole("combobox").selectOption("completed");
  await expect(
    page
      .getByRole("region", { name: "Completed", exact: true })
      .getByText("Review the launch proposal"),
  ).toBeVisible();
  await expect.poll(() => state.tasks[0].status).toBe("completed");
});

test("rolls back a failed optimistic status change and shows an error", async ({
  page,
}) => {
  const state = await mockApi(page);
  state.failUpdate = true;
  await page.goto("/");
  await page
    .getByRole("combobox", { name: "Status for Review the launch proposal" })
    .selectOption("completed");
  await expect(
    page
      .getByRole("region", { name: "Pending", exact: true })
      .getByText("Review the launch proposal"),
  ).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("Could not save status.");
});

test("searches tasks without a source filter", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await expect(
    page.getByRole("combobox", { name: "Filter by source" }),
  ).toHaveCount(0);
  await expect(page.getByTestId("task-card")).toHaveCount(3);
  const searchToggle = page.getByRole("button", {
    name: "Search tasks",
    exact: true,
  });
  const searchInput = page.getByRole("textbox", { name: "Search tasks" });
  await expect(searchInput).toHaveCount(0);
  await expect(searchToggle).toHaveAttribute("aria-expanded", "false");
  await searchToggle.click();
  await expect(searchInput).toBeFocused();
  await expect(
    page.getByRole("button", { name: "Close search" }),
  ).toHaveAttribute("aria-expanded", "true");
  await page.getByRole("textbox", { name: "Search tasks" }).fill("onboarding");
  await expect(page.getByTestId("task-card")).toHaveCount(1);
  await page
    .getByRole("textbox", { name: "Search tasks" })
    .fill("does not exist");
  await expect(page.getByTestId("task-card")).toHaveCount(0);
  await expect(page.getByText("No matching tasks")).toHaveCount(3);
  await page.keyboard.press("Escape");
  await expect(searchInput).toHaveCount(0);
  await expect(searchToggle).toBeFocused();
  await expect(page.getByTestId("task-card")).toHaveCount(3);
  await searchToggle.click();
  await searchInput.fill("onboarding");
  await page.getByRole("button", { name: "Close search" }).click();
  await expect(searchInput).toHaveCount(0);
  await expect(page.getByTestId("task-card")).toHaveCount(3);
});

test("refetches after new-task and reconnect events", async ({ page }) => {
  const state = await mockApi(page);
  await page.goto("/");
  await expect(page.getByTestId("task-card")).toHaveCount(3);
  state.tasks.push({
    ...initial[0],
    id: "4",
    text: "A new task from Telegram",
  });
  await page.evaluate(() =>
    window.taskbotTestStream.dispatchEvent(new MessageEvent("task_changed")),
  );
  await expect(page.getByText("A new task from Telegram")).toBeVisible();
  state.tasks.push({
    ...initial[0],
    id: "5",
    text: "Captured while disconnected",
  });
  await page.evaluate(() =>
    window.taskbotTestStream.dispatchEvent(new MessageEvent("ready")),
  );
  await expect(page.getByText("Captured while disconnected")).toBeVisible();
});

test("exchanges a sign-in token only after confirmation and removes it from the URL", async ({
  page,
}) => {
  const state = await mockApi(page, { signedIn: false });
  await page.goto("/#token=private-one-time-token");
  await expect(
    page.getByRole("heading", { name: "Sign in with this link?" }),
  ).toBeVisible();
  await expect(page).toHaveURL("/");
  expect(state.exchanged).toBe("");
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(page.getByTestId("task-card")).toHaveCount(3);
  expect(state.exchanged).toBe("private-one-time-token");
  await expect(page).toHaveURL("/dashboard");
});

test("a shared sign-in link cannot silently replace the current session", async ({
  page,
}) => {
  const state = await mockApi(page);
  await page.goto("/dashboard#token=someone-elses-token");
  await expect(
    page.getByRole("heading", { name: "Sign in with this link?" }),
  ).toBeVisible();
  await expect(page.getByRole("status")).toContainText(
    "You are signed in as test1@hire.me",
  );
  await expect(page).toHaveURL("/dashboard");
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.getByTestId("task-card")).toHaveCount(3);
  expect(state.exchanged).toBe("");
  await expect(page).toHaveURL("/dashboard");
});

test("shows sign-in instructions and supports logout", async ({ page }) => {
  const state = await mockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Log out" }).click();
  await expect(
    page.getByRole("heading", { name: "Log out of your account?" }),
  ).toBeVisible();
  await expect(page).toHaveURL("/dashboard");
  await page
    .getByRole("button", { name: "Log out on this device", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Your tasks. One web dashboard." }),
  ).toBeVisible();
  await expect(page.getByLabel("Email", { exact: true })).toBeVisible();
  expect(state.logoutPath).toBe("/api/auth/logout");
  await expect(page.getByRole("status")).toHaveText(
    "You have been logged out.",
  );
  await expect(page).toHaveURL("/login");
  await page.reload();
  await expect(page.getByLabel("Email", { exact: true })).toBeVisible();
  await expect(page.getByText("You have been logged out.")).toHaveCount(0);
});

test("logout popup cancels without changing the board and offers all-device logout", async ({
  page,
}) => {
  const state = await mockApi(page);
  await page.goto("/dashboard?view=due");
  const trigger = page.getByRole("button", { name: "Log out", exact: true });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "Log out of your account?" });
  await expect(dialog).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Cancel logout" }),
  ).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(
    dialog.getByRole("button", { name: "Log out on all devices" }),
  ).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await expect(page).toHaveURL("/dashboard?view=due");
  await trigger.click();
  await page.getByRole("button", { name: "Cancel logout" }).click();
  expect(state.logoutPath).toBe("");
  await trigger.click();
  state.failLogout = true;
  await dialog.getByRole("button", { name: "Log out on all devices" }).click();
  await expect(dialog.getByRole("alert")).toContainText("Could not log out");
  await expect(dialog).toBeVisible();
  state.failLogout = false;
  await dialog.getByRole("button", { name: "Log out on all devices" }).click();
  await expect(page.getByLabel("Email", { exact: true })).toBeVisible();
  expect(state.logoutPath).toBe("/api/auth/logout-all");
  await expect(page.getByRole("status")).toHaveText(
    "You have been logged out.",
  );
  await expect(page).toHaveURL("/login");
  await page.reload();
  await expect(page.getByLabel("Email", { exact: true })).toBeVisible();
  await expect(page.getByText("You have been logged out.")).toHaveCount(0);
});

test("direct logout links open a popup over the dashboard", async ({
  page,
}) => {
  await mockApi(page);
  await page.goto("/logout");
  await expect(
    page.getByRole("dialog", { name: "Log out of your account?" }),
  ).toBeVisible();
  await expect(page.getByTestId("task-card")).toHaveCount(3);
  await page.getByRole("button", { name: "Cancel logout" }).click();
  await expect(page).toHaveURL("/dashboard");
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("shows empty and help states with keyboard dismissal", async ({
  page,
}) => {
  await mockApi(page, { empty: true });
  await page.goto("/");
  await expect(page.getByText("A little breathing room")).toHaveCount(3);
  await expect(
    page.getByRole("button", { name: "Add via Telegram" }),
  ).toHaveCount(0);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("button", { name: "How it works" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("shows a recoverable task-loading error", async ({ page }) => {
  await mockApi(page, { taskFailure: true });
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText("Could not load tasks.");
  await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
});

test("offers a website login button that opens the bot sign-in flow", async ({
  page,
}) => {
  await mockApi(page, { signedIn: false });
  await page.goto("/");
  await expect(
    page.getByRole("link", { name: "Log in with Telegram" }),
  ).toHaveAttribute("href", "https://t.me/taskline_test_bot?start=login");
});

test("opens the dedicated web dashboard directly", async ({ page }) => {
  await mockApi(page);
  await page.goto("/dashboard");
  await expect(page.getByRole("region", { name: "Task board" })).toBeVisible();
  await expect(page.getByTestId("task-card")).toHaveCount(3);
  await page.reload();
  await expect(page.getByTestId("task-card")).toHaveCount(3);
  await expect(page).toHaveURL("/dashboard");
});

test("protects the web dashboard with Telegram sign-in", async ({ page }) => {
  await mockApi(page, { signedIn: false });
  await page.goto("/dashboard");
  await expect(
    page.getByRole("heading", { name: "Your tasks. One web dashboard." }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Log in with Telegram" }),
  ).toBeVisible();
  await expect(page.getByTestId("task-card")).toHaveCount(0);
  await expect(page).toHaveURL("/login");
});

for (const [email, password] of [
  ["test1@hire.me", "hireme1"],
  ["test2@hire.me", "hireme2"],
]) {
  test(`logs into the web dashboard with ${email}`, async ({ page }) => {
    const state = await mockApi(page, { signedIn: false });
    await page.goto("/login");
    await page.getByLabel("Email", { exact: true }).fill(email);
    await page.getByLabel("Password", { exact: true }).fill(password);
    await page.getByRole("button", { name: "Log in", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: /^My Tasks/ }),
    ).toBeVisible();
    await expect(page).toHaveURL("/dashboard");
    expect(state.account.email).toBe(email);
  });
}

test("submits the visible credentials after autofill replaces earlier values", async ({
  page,
}) => {
  const state = await mockApi(page, { signedIn: false });
  await page.goto("/login");
  await page.getByLabel("Email", { exact: true }).fill("test2@hier.me");
  await page.getByLabel("Password", { exact: true }).fill("older-password");
  await page.evaluate(() => {
    const setter = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )!.set!;
    setter.call(document.getElementById("login-email"), "test2@hire.me");
    setter.call(document.getElementById("login-password"), "hireme2");
  });
  await expect(page.getByLabel("Email", { exact: true })).toHaveValue(
    "test2@hire.me",
  );
  await expect(page.getByLabel("Password", { exact: true })).toHaveValue(
    "hireme2",
  );
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await expect(page.getByRole("heading", { name: /^My Tasks/ })).toBeVisible();
  expect(state.account.email).toBe("test2@hire.me");
});

test("rejects an incorrect website password without exposing tasks", async ({
  page,
}) => {
  await mockApi(page, { signedIn: false });
  await page.goto("/login");
  await page.getByLabel("Email", { exact: true }).fill("test1@hire.me");
  await page.getByLabel("Password", { exact: true }).fill("incorrect");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(
    "Email or password is incorrect",
  );
  await expect(page.getByTestId("task-card")).toHaveCount(0);
  await expect(page.getByLabel("Password", { exact: true })).toHaveValue("");
});

test("provides My tasks, Open Telegram, and Log out controls", async ({
  page,
}) => {
  await mockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "My tasks", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "Task board" }),
  ).toBeInViewport();
  await expect(
    page.getByRole("button", { name: "Open Telegram", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Log out", exact: true }),
  ).toBeVisible();
});

test("creates a dashboard task in the selected Kanban column and persists it", async ({
  page,
}) => {
  await mockApi(page, { empty: true });
  await page.goto("/dashboard");
  await page.getByRole("button", { name: "Add task", exact: true }).click();
  await expect(page.getByLabel("New task status")).toHaveValue("pending");
  await page
    .getByLabel("Task description", { exact: true })
    .fill("Prepare the demo\nShare it with the team");
  await page.getByLabel("Task title", { exact: true }).fill("Prepare the demo");
  await page.getByLabel("New task status").selectOption("in_progress");
  await page.getByRole("button", { name: "Create task", exact: true }).click();
  const card = page
    .getByRole("region", { name: "In progress", exact: true })
    .getByTestId("task-card");
  await expect(card).toContainText("Prepare the demo");
  await expect(card).toContainText("Personal");
  await expect(page.getByRole("form", { name: "New task" })).toHaveCount(0);
  await page.reload();
  await expect(page.getByTestId("task-card")).toHaveCount(1);
  await expect(page.getByTestId("task-card")).toContainText(
    "Share it with the team",
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});

test("retains a failed creation for retry and prevents blank tasks", async ({
  page,
}) => {
  const state = await mockApi(page);
  state.failCreate = true;
  await page.goto("/dashboard");
  await page.getByRole("button", { name: "Add a task", exact: true }).click();
  const create = page.getByRole("button", { name: "Create task", exact: true });
  await page.getByLabel("Task description", { exact: true }).fill("   ");
  await expect(create).toBeDisabled();
  await page
    .getByLabel("Task title", { exact: true })
    .fill("Keep my unsaved task");
  await page
    .getByLabel("Task description", { exact: true })
    .fill("Keep my unsaved task");
  await create.click();
  await expect(page.getByRole("alert")).toContainText("Could not create task.");
  await expect(
    page.getByLabel("Task description", { exact: true }),
  ).toHaveValue("Keep my unsaved task");
  state.failCreate = false;
  await create.click();
  await expect(page.getByTestId("task-card")).toHaveCount(4);
});

test("keeps a short title above the description and edits and searches both", async ({
  page,
}) => {
  const state = await mockApi(page, { empty: true });
  await page.goto("/dashboard");
  await page.getByRole("button", { name: "Add task", exact: true }).click();
  const create = page.getByRole("button", { name: "Create task", exact: true });
  await page
    .getByLabel("Task description", { exact: true })
    .fill("Check the release checklist and share feedback.");
  await expect(create).toBeDisabled();
  await page.getByLabel("Task title", { exact: true }).fill("Release review");
  await create.click();
  const card = page.getByTestId("task-card");
  await expect(
    card.getByRole("heading", { name: "Release review", exact: true }),
  ).toBeVisible();
  await expect(card.locator(".task-text")).toHaveText(
    "Check the release checklist and share feedback.",
  );
  await page
    .getByRole("button", { name: "Open task Release review", exact: true })
    .click();
  await expect(page.getByLabel("Task title", { exact: true })).toHaveValue(
    "Release review",
  );
  await page.getByLabel("Task title", { exact: true }).fill("Ship the release");
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(page.getByRole("dialog").getByRole("status")).toContainText(
    "Changes saved.",
  );
  await page.getByRole("button", { name: "Close task", exact: true }).click();
  expect(state.tasks[0].title).toBe("Ship the release");
  expect(state.tasks[0].text).toBe(
    "Check the release checklist and share feedback.",
  );
  await page.reload();
  await expect(
    card.getByRole("heading", { name: "Ship the release", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Search tasks", exact: true }).click();
  await page
    .getByRole("textbox", { name: "Search tasks" })
    .fill("Ship the release");
  await expect(card).toHaveCount(1);
  await page
    .getByRole("textbox", { name: "Search tasks" })
    .fill("share feedback");
  await expect(card).toHaveCount(1);
});

for (const source of ["text", "voice", "dashboard"] as const) {
  test(`confirms and deletes a ${source} task with cancellation available`, async ({
    page,
  }) => {
    const state = await mockApi(page);
    state.tasks[0].source = source;
    await page.goto("/dashboard");
    const card = page
      .getByTestId("task-card")
      .filter({ hasText: initial[0].text });
    await card
      .getByRole("button", { name: `Delete ${initial[0].text}`, exact: true })
      .click();
    await card.getByRole("button", { name: "Keep task" }).click();
    await expect(card).toBeVisible();
    await card
      .getByRole("button", { name: `Delete ${initial[0].text}`, exact: true })
      .click();
    await card
      .getByRole("button", { name: "Delete task", exact: true })
      .click();
    await expect(page.getByTestId("task-card")).toHaveCount(2);
    await page.reload();
    await expect(page.getByText(initial[0].text, { exact: true })).toHaveCount(
      0,
    );
    expect(state.tasks).toHaveLength(2);
  });
}

test("keeps a task visible when deletion fails and supports retry", async ({
  page,
}) => {
  const state = await mockApi(page);
  state.failDelete = true;
  await page.goto("/dashboard");
  const card = page
    .getByTestId("task-card")
    .filter({ hasText: initial[0].text });
  await card
    .getByRole("button", { name: `Delete ${initial[0].text}`, exact: true })
    .click();
  await card.getByRole("button", { name: "Delete task", exact: true }).click();
  await expect(card.getByRole("alert")).toContainText("Could not delete task.");
  await expect(page.getByTestId("task-card")).toHaveCount(3);
  state.failDelete = false;
  await card.getByRole("button", { name: "Delete task", exact: true }).click();
  await expect(page.getByTestId("task-card")).toHaveCount(2);
});

test("removes tasks deleted in another session after a live update", async ({
  page,
}) => {
  const state = await mockApi(page);
  await page.goto("/dashboard");
  await expect(page.getByTestId("task-card")).toHaveCount(3);
  state.tasks.shift();
  await page.evaluate(() =>
    window.taskbotTestStream.dispatchEvent(new MessageEvent("task_changed")),
  );
  await expect(page.getByTestId("task-card")).toHaveCount(2);
});

async function startCardDrag(page: Page, destination: string) {
  const handle = page.getByRole("button", {
    name: `Move ${initial[0].text}`,
    exact: true,
  });
  const target = page.getByRole("region", { name: destination, exact: true });
  const start = (await handle.boundingBox())!;
  const end = (await target.boundingBox())!;
  await page.mouse.move(start.x + start.width / 2, start.y + start.height / 2);
  await page.mouse.down();
  await page.mouse.move(
    start.x + start.width / 2 + 10,
    start.y + start.height / 2,
    { steps: 3 },
  );
  await page.mouse.move(end.x + end.width / 2, end.y + 25, { steps: 15 });
  await expect(target).toHaveClass(/drop-target/);
  return target;
}

test("drags a task through every column transition and persists after reload", async ({
  page,
}) => {
  const state = await mockApi(page);
  await page.setViewportSize({
    width: page.viewportSize()!.width,
    height: 2200,
  });
  await page.goto("/dashboard");
  for (const [name, status] of [
    ["In progress", "in_progress"],
    ["Completed", "completed"],
    ["Pending", "pending"],
    ["Completed", "completed"],
    ["In progress", "in_progress"],
    ["Pending", "pending"],
  ]) {
    const target = await startCardDrag(page, name);
    await page.mouse.up();
    await expect(
      target.getByText(initial[0].text, { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("combobox", {
        name: `Status for ${initial[0].text}`,
        exact: true,
      }),
    ).toBeEnabled();
    await expect.poll(() => state.tasks[0].status).toBe(status);
  }
  expect(state.statusRequests).toBe(6);
  await page.reload();
  await expect(
    page
      .getByRole("region", { name: "Pending", exact: true })
      .getByText(initial[0].text),
  ).toBeVisible();
});

test("supports dropping into an empty column and dragging from the card body", async ({
  page,
}) => {
  const state = await mockApi(page);
  state.tasks = [structuredClone(initial[0])];
  await page.setViewportSize({
    width: page.viewportSize()!.width,
    height: 2200,
  });
  await page.goto("/dashboard");
  const body = page.getByTestId("task-card").locator(".task-title");
  const target = page.getByRole("region", { name: "Completed", exact: true });
  const start = (await body.boundingBox())!;
  const end = (await target.boundingBox())!;
  await page.mouse.move(start.x + 10, start.y + 5);
  await page.mouse.down();
  await page.mouse.move(start.x + 20, start.y + 5, { steps: 3 });
  await page.mouse.move(end.x + end.width / 2, end.y + 25, { steps: 15 });
  await page.mouse.up();
  await expect(target.getByText(initial[0].text)).toBeVisible();
  await expect.poll(() => state.tasks[0].status).toBe("completed");
});

test("rolls back a failed drag and shows the save error on the card", async ({
  page,
}) => {
  const state = await mockApi(page);
  state.failUpdate = true;
  await page.setViewportSize({
    width: page.viewportSize()!.width,
    height: 2200,
  });
  await page.goto("/dashboard");
  await startCardDrag(page, "Completed");
  await page.mouse.up();
  const card = page
    .getByRole("region", { name: "Pending", exact: true })
    .getByTestId("task-card");
  await expect(card).toContainText(initial[0].text);
  await expect(card.getByRole("alert")).toContainText("Could not save status.");
  expect(state.tasks[0].status).toBe("pending");
});

test("cancels a drag and ignores drops outside the board or in the same column", async ({
  page,
}) => {
  const state = await mockApi(page);
  await page.setViewportSize({
    width: page.viewportSize()!.width,
    height: 2200,
  });
  await page.goto("/dashboard");
  await startCardDrag(page, "Completed");
  await page.keyboard.press("Escape");
  await page.mouse.up();
  await startCardDrag(page, "Completed");
  await page.mouse.move(10, 10, { steps: 15 });
  await page.mouse.up();
  await startCardDrag(page, "Pending");
  await page.mouse.up();
  await expect(
    page
      .getByRole("region", { name: "Pending", exact: true })
      .getByText(initial[0].text),
  ).toBeVisible();
  expect(state.statusRequests).toBe(0);
});

test("moves tasks by keyboard using Space and arrow keys", async ({ page }) => {
  const state = await mockApi(page);
  await page.setViewportSize({
    width: page.viewportSize()!.width,
    height: 2200,
  });
  await page.goto("/dashboard");
  const handle = page.getByRole("button", {
    name: `Move ${initial[0].text}`,
    exact: true,
  });
  await handle.focus();
  await page.keyboard.press("Space");
  await expect(handle).toHaveAttribute("aria-pressed", "true");
  await expect(
    page.getByRole("region", { name: "Pending", exact: true }),
  ).toHaveClass(/drop-target/);
  await page.keyboard.press("ArrowRight");
  const target = page.getByRole("region", { name: "In progress", exact: true });
  await expect(target).toHaveClass(/drop-target/);
  await page.keyboard.press("Space");
  await expect(target.getByText(initial[0].text)).toBeVisible();
  await expect.poll(() => state.tasks[0].status).toBe("in_progress");
});

test("moves a task with a touch gesture on mobile", async ({ page }, info) => {
  test.skip(
    info.project.name !== "mobile",
    "Touch input is exercised in the mobile project",
  );
  const state = await mockApi(page);
  await page.setViewportSize({ width: 390, height: 2200 });
  await page.goto("/dashboard");
  const handle = page.getByRole("button", {
    name: `Move ${initial[0].text}`,
    exact: true,
  });
  const target = page.getByRole("region", { name: "In progress", exact: true });
  const start = (await handle.boundingBox())!;
  const end = (await target.boundingBox())!;
  const session = await page.context().newCDPSession(page);
  const x = start.x + start.width / 2;
  const y = start.y + start.height / 2;
  await session.send("Input.dispatchTouchEvent", {
    type: "touchStart",
    touchPoints: [{ x, y }],
  });
  await expect(handle).toHaveAttribute("aria-pressed", "true");
  await session.send("Input.dispatchTouchEvent", {
    type: "touchMove",
    touchPoints: [{ x: end.x + end.width / 2, y: end.y + 25 }],
  });
  await expect(target).toHaveClass(/drop-target/);
  await session.send("Input.dispatchTouchEvent", {
    type: "touchEnd",
    touchPoints: [],
  });
  await expect(target.getByText(initial[0].text)).toBeVisible();
  await expect.poll(() => state.tasks[0].status).toBe("in_progress");
  await session.detach();
});

test("manager creates a project, adds a developer, assigns a deadline, and developer comments", async ({
  page,
}) => {
  const state = await mockApi(page, { empty: true, role: "project_manager" });
  await page.goto("/dashboard");
  await page.getByRole("button", { name: "New project", exact: true }).click();
  await page.getByLabel("Project name", { exact: true }).fill("Launch");
  await page
    .getByRole("button", { name: "Create project", exact: true })
    .click();
  await expect(page.getByRole("heading", { name: /^Launch/ })).toBeVisible();
  await page
    .getByRole("button", { name: "Options for Launch", exact: true })
    .click();
  await page.getByRole("menuitem", { name: "Info", exact: true }).click();
  await page.getByLabel("Person email", { exact: true }).fill("test2@hire.me");
  await page.getByRole("button", { name: "Add person", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("Jamie Developer");
  await page.getByRole("button", { name: "Close project info" }).click();
  await page.getByRole("button", { name: "Add task", exact: true }).click();
  await page
    .getByLabel("Task description", { exact: true })
    .fill("Build the launch page");
  await page
    .getByLabel("Task title", { exact: true })
    .fill("Build the launch page");
  await page.getByLabel("Assign task to", { exact: true }).click();
  await page.getByRole("checkbox", { name: /Jamie Developer/ }).check();
  await page.getByRole("button", { name: "Done", exact: true }).click();
  await page
    .getByLabel("Task deadline", { exact: true })
    .fill(new Date(Date.now() + 2 * 86400000).toISOString().slice(0, 16));
  await page.getByRole("button", { name: "Create task", exact: true }).click();
  await expect(page.getByTestId("task-card")).toContainText("@Jamie Developer");
  expect(state.tasks[0].assignee_id).toBe("user-2");
  await page.getByRole("button", { name: "My tasks", exact: true }).click();
  await expect(page.getByTestId("task-card")).toHaveCount(0);
  state.account = state.accounts[1];
  await page.reload();
  await expect(page.getByTestId("task-card")).toHaveCount(1);
  await expect(
    page.getByRole("button", { name: "Project Launch", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "New project", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", {
      name: "Delete Build the launch page",
      exact: true,
    }),
  ).toHaveCount(0);
  await page
    .getByRole("button", {
      name: "Open task Build the launch page",
      exact: true,
    })
    .click();
  await expect(
    page.getByRole("button", { name: "Save changes", exact: true }),
  ).toHaveCount(0);
  await page
    .getByLabel("Write a comment", { exact: true })
    .fill("Ready for review");
  await page.getByRole("button", { name: "Post comment", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "Task comments" }),
  ).toContainText("Ready for review");
  await page.getByRole("button", { name: "Close task" }).click();
  await page
    .getByRole("combobox", {
      name: "Status for Build the launch page",
      exact: true,
    })
    .selectOption("completed");
  await expect(
    page.getByRole("region", { name: "Completed", exact: true }),
  ).toContainText("Build the launch page");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("due soon collects upcoming and overdue incomplete tasks and updates on completion", async ({
  page,
}) => {
  const state = await mockApi(page);
  state.tasks = [
    {
      ...initial[0],
      due_at: new Date(Date.now() + 3 * 86400000).toISOString(),
    },
    { ...initial[1], due_at: new Date(Date.now() - 86400000).toISOString() },
    { ...initial[2], due_at: new Date(Date.now() + 86400000).toISOString() },
    {
      ...initial[0],
      id: "later",
      text: "Next week",
      due_at: new Date(Date.now() + 6 * 86400000).toISOString(),
    },
  ];
  await page.goto("/dashboard");
  await page.getByRole("button", { name: "Due soon", exact: true }).click();
  await expect(page.getByTestId("task-card")).toHaveCount(2);
  await expect(page.getByText("Next week", { exact: true })).toHaveCount(0);
  await expect(
    page.getByTestId("task-card").filter({ hasText: initial[1].text }),
  ).toContainText("Overdue");
  const saved = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/api/tasks/${initial[0].id}`) &&
      response.request().method() === "PATCH" &&
      response.ok(),
  );
  await page
    .getByRole("combobox", {
      name: `Status for ${initial[0].text}`,
      exact: true,
    })
    .selectOption("completed");
  await expect(page.getByTestId("task-card")).toHaveCount(1);
  await saved;
  await page.reload();
  await expect(page.getByRole("heading", { name: /^Due soon/ })).toBeVisible();
  await expect(page.getByTestId("task-card")).toHaveCount(1);
  const screenshotName =
    page.viewportSize()!.width < 600
      ? "../output/dashboard/due-soon-mobile.png"
      : "../output/dashboard/due-soon-desktop.png";
  await page.screenshot({
    path: screenshotName,
    fullPage: true,
  });
  await expect(
    page.getByText("Drag tasks here to mark them as completed"),
  ).toBeVisible();
  const completeBtn = page.getByRole("button", {
    name: `Move ${initial[1].title} to completed`,
  });
  await expect(completeBtn).toBeVisible();
  await completeBtn.click();
  await expect(page.getByTestId("task-card")).toHaveCount(0);
});

test("due soon supports dragging to Move to Completed drop zone", async ({
  page,
}) => {
  const state = await mockApi(page);
  await page.setViewportSize({
    width: page.viewportSize()!.width,
    height: 2200,
  });
  state.tasks = [
    {
      ...initial[0],
      due_at: new Date(Date.now() + 3 * 86400000).toISOString(),
    },
  ];
  await page.goto("/dashboard?view=due");
  await expect(page.getByTestId("task-card")).toHaveCount(1);
  const handle = page.getByRole("button", {
    name: `Move ${initial[0].text}`,
    exact: true,
  });
  const target = page.getByRole("region", {
    name: "Move to Completed",
    exact: true,
  });
  const start = (await handle.boundingBox())!;
  const end = (await target.boundingBox())!;
  await page.mouse.move(start.x + start.width / 2, start.y + start.height / 2);
  await page.mouse.down();
  await page.mouse.move(
    start.x + start.width / 2 + 10,
    start.y + start.height / 2,
    { steps: 3 },
  );
  await page.mouse.move(end.x + end.width / 2, end.y + 35, { steps: 15 });
  await expect(target).toHaveClass(/drop-target/);
  await page.mouse.up();
  await expect(page.getByTestId("task-card")).toHaveCount(0);
});

test("manager edits assignment and deadline from task details", async ({
  page,
}) => {
  const state = await mockApi(page, { role: "project_manager" });
  state.projects = [
    { id: "project-1", name: "Launch", owner_id: "user-1", can_manage: true },
  ];
  state.members["project-1"] = state.accounts;
  state.tasks[0] = {
    ...state.tasks[0],
    project_id: "project-1",
    project_name: "Launch",
    assignee_id: "user-1",
    assignee: state.accounts[0],
  };
  await page.goto("/dashboard?project=project-1");
  await page
    .getByRole("button", { name: `Open task ${initial[0].text}`, exact: true })
    .click();
  await page.getByLabel("Edit assignee", { exact: true }).click();
  await page.getByRole("checkbox", { name: /Alex Morgan/ }).uncheck();
  await page.getByRole("checkbox", { name: /Jamie Developer/ }).check();
  await page.getByRole("button", { name: "Done", exact: true }).click();
  await page
    .getByLabel("Edit deadline", { exact: true })
    .fill(new Date(Date.now() + 86400000).toISOString().slice(0, 16));
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(page.getByText("Changes saved.", { exact: true })).toBeVisible();
  expect(state.tasks[0].assignee_id).toBe("user-2");
  expect(state.tasks[0].due_at).toBeTruthy();
  await page.getByRole("button", { name: "Close task" }).click();
  await expect(page.getByTestId("task-card")).toContainText("@Jamie Developer");
});

test("manager creates a task for multiple people and edits or clears the selection", async ({
  page,
}) => {
  const state = await mockApi(page, { empty: true, role: "project_manager" });
  state.projects = [
    { id: "project-1", name: "Launch", owner_id: "user-1", can_manage: true },
  ];
  state.members["project-1"] = state.accounts;
  await page.goto("/dashboard?project=project-1");
  await page.getByRole("button", { name: "Add task", exact: true }).click();
  await page.getByLabel("Task title", { exact: true }).fill("Build together");
  await page
    .getByLabel("Task description", { exact: true })
    .fill("A shared task");
  const selector = page.getByLabel("Assign task to", { exact: true });
  await selector.focus();
  await page.keyboard.press("Enter");
  await page.getByRole("checkbox", { name: /Alex Morgan/ }).check();
  await page.getByRole("checkbox", { name: /Jamie Developer/ }).check();
  await expect(selector).toContainText("Alex Morgan, Jamie Developer");
  await page.screenshot({
    path: `../output/dashboard/multiple-assignees-${page.viewportSize()!.width < 600 ? "mobile" : "desktop"}.png`,
    fullPage: true,
  });
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("checkbox", { name: /Jamie Developer/ }),
  ).not.toBeVisible();
  await page.getByRole("button", { name: "Create task", exact: true }).click();
  await expect(page.getByTestId("task-card")).toContainText("@Alex Morgan");
  await expect(page.getByTestId("task-card")).toContainText("@Jamie Developer");
  expect(state.tasks[0].assignee_ids).toEqual(["user-1", "user-2"]);
  await page.reload();
  await page
    .getByRole("button", { name: "Open task Build together", exact: true })
    .click();
  await page.getByLabel("Edit assignee", { exact: true }).click();
  await expect(
    page.getByRole("checkbox", { name: /Alex Morgan/ }),
  ).toBeChecked();
  await expect(
    page.getByRole("checkbox", { name: /Jamie Developer/ }),
  ).toBeChecked();
  await page.getByRole("checkbox", { name: /Alex Morgan/ }).uncheck();
  await page.getByRole("button", { name: "Done", exact: true }).click();
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(page.getByText("Changes saved.", { exact: true })).toBeVisible();
  expect(state.tasks[0].assignee_ids).toEqual(["user-2"]);
  await page.getByLabel("Edit assignee", { exact: true }).click();
  await page
    .getByRole("button", { name: "Clear selection", exact: true })
    .click();
  await page.getByRole("button", { name: "Done", exact: true }).click();
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect.poll(() => state.tasks[0].assignee_ids).toEqual([]);
  await page.getByRole("button", { name: "Close task", exact: true }).click();
  await expect(page.getByTestId("task-card")).not.toContainText(
    "@Jamie Developer",
  );
  await page.reload();
  await page
    .getByRole("button", { name: "Open task Build together", exact: true })
    .click();
  await expect(page.getByLabel("Edit assignee", { exact: true })).toContainText(
    "Unassigned",
  );
});

test("assignee dropdown keeps the form still and scrolls after three people", async ({
  page,
}) => {
  const state = await mockApi(page, { empty: true, role: "project_manager" });
  state.projects = [
    { id: "project-1", name: "Launch", owner_id: "user-1", can_manage: true },
  ];
  state.members["project-1"] = [
    ...state.accounts,
    ...Array.from({ length: 4 }, (_, index) => ({
      id: `extra-${index}`,
      name: `Teammate ${index + 1}`,
      email: `teammate${index + 1}@example.test`,
    })),
  ];
  await page.goto("/dashboard?project=project-1");
  await page.getByRole("button", { name: "Add task", exact: true }).click();
  const positions = () =>
    page.locator(".task-composer").evaluate((form) => {
      const relativeY = (selector: string) =>
        form.querySelector(selector)!.getBoundingClientRect().top -
        form.getBoundingClientRect().top;
      return {
        height: form.getBoundingClientRect().height,
        deadline: relativeY('[aria-label="Task deadline"]'),
        actions: relativeY(".composer-actions"),
      };
    });
  const before = await positions();
  const selector = page.getByLabel("Assign task to", { exact: true });
  await selector.click();
  expect(await positions()).toEqual(before);
  const list = page.locator(".assignee-list");
  const dimensions = await list.evaluate((element) => ({
    viewport: element.clientHeight,
    content: element.scrollHeight,
    row: element.firstElementChild!.getBoundingClientRect().height,
  }));
  expect(dimensions.viewport).toBe(dimensions.row * 3);
  expect(dimensions.content).toBeGreaterThan(dimensions.viewport);
  await page.getByRole("checkbox", { name: /Jamie Developer/ }).check();
  await page.getByRole("checkbox", { name: /Teammate 4/ }).check();
  await expect
    .poll(() => list.evaluate((element) => element.scrollTop))
    .toBeGreaterThan(0);
  await expect(selector).toContainText("Jamie Developer, Teammate 4");
  expect(await positions()).toEqual(before);
  await list.evaluate((element) => {
    element.scrollTop = 0;
  });
  await page.screenshot({
    path: `../output/dashboard/assignee-dropdown-${page.viewportSize()!.width < 600 ? "mobile" : "desktop"}.png`,
    fullPage: true,
  });
  await page.getByRole("button", { name: "Done", exact: true }).click();
  await expect(
    page.getByRole("group", { name: "Project assignees" }),
  ).not.toBeVisible();
});

test("changing projects clears the previous assignee selection", async ({
  page,
}) => {
  const state = await mockApi(page, { empty: true, role: "project_manager" });
  state.projects = [
    { id: "project-1", name: "Launch", owner_id: "user-1", can_manage: true },
    { id: "project-2", name: "Next", owner_id: "user-1", can_manage: true },
  ];
  state.members["project-1"] = state.accounts;
  state.members["project-2"] = [state.accounts[0]];
  await page.goto("/dashboard?project=project-1");
  await page.getByRole("button", { name: "Add task", exact: true }).click();
  await page.getByLabel("Assign task to", { exact: true }).click();
  await page.getByRole("checkbox", { name: /Jamie Developer/ }).check();
  await page
    .getByLabel("Task project", { exact: true })
    .selectOption("project-2");
  await expect(
    page.getByLabel("Assign task to", { exact: true }),
  ).toContainText("Unassigned");
  await page.getByLabel("Assign task to", { exact: true }).click();
  await expect(
    page.getByRole("checkbox", { name: /Jamie Developer/ }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("checkbox", { name: /Alex Morgan/ }),
  ).not.toBeChecked();
});

test("manager archives a project, views its saved work, and restores it after reload", async ({
  page,
}) => {
  const state = await mockApi(page, { role: "project_manager" });
  state.projects = [
    { id: "project-1", name: "Launch", owner_id: "user-1", can_manage: true },
  ];
  state.members["project-1"] = state.accounts;
  state.tasks[0] = {
    ...state.tasks[0],
    project_id: "project-1",
    project_name: "Launch",
    assignee_ids: ["user-1", "user-2"],
  };
  await page.goto("/dashboard?project=project-1");
  await expect(page.getByTestId("task-card")).toHaveCount(1);
  await page
    .getByRole("button", { name: "Options for Launch", exact: true })
    .click();
  await page
    .getByRole("menuitem", { name: "Archive project", exact: true })
    .click();
  const confirmation = page.getByRole("dialog", { name: "Archive project?" });
  await expect(confirmation).toContainText("Launch");
  await expect(
    confirmation.getByRole("button", { name: "No", exact: true }),
  ).toBeFocused();
  expect(state.projects[0].archived_at).toBeUndefined();
  await confirmation.getByRole("button", { name: "No", exact: true }).click();
  await expect(confirmation).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Options for Launch", exact: true }),
  ).toBeFocused();
  expect(state.projects[0].archived_at).toBeUndefined();
  await expect(page.getByTestId("task-card")).toHaveCount(1);
  await page
    .getByRole("button", { name: "Options for Launch", exact: true })
    .click();
  await page
    .getByRole("menuitem", { name: "Archive project", exact: true })
    .click();
  await page.keyboard.press("Escape");
  await expect(confirmation).toHaveCount(0);
  expect(state.projects[0].archived_at).toBeUndefined();
  await page
    .getByRole("button", { name: "Options for Launch", exact: true })
    .click();
  await page
    .getByRole("menuitem", { name: "Archive project", exact: true })
    .click();
  await confirmation.getByRole("button", { name: "Yes", exact: true }).click();
  await expect(confirmation).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Project Launch", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByTestId("task-card").filter({ hasText: initial[0].title }),
  ).toHaveCount(0);
  expect(state.projects[0].archived_at).toBeTruthy();
  expect(state.tasks[0].status).toBe("pending");
  await page.reload();
  await page
    .getByRole("button", { name: "Show archived projects", exact: true })
    .click();
  const archive = page.getByRole("dialog", {
    name: "Archived projects",
    exact: true,
  });
  await expect(archive).toContainText("Launch");
  await page.screenshot({
    path: `../output/dashboard/archived-projects-${page.viewportSize()!.width < 600 ? "mobile" : "desktop"}.png`,
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "View archived project Launch", exact: true })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "This project is archived" }),
  ).toBeVisible();
  await expect(page.getByTestId("task-card")).toHaveCount(1);
  await expect(
    page.getByLabel(`Status for ${initial[0].title}`, { exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Add task", exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Show archived projects", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Restore project Launch", exact: true })
    .click();
  await expect(archive).not.toBeVisible();
  await expect(
    page.getByRole("button", { name: "Project Launch", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByLabel(`Status for ${initial[0].title}`, { exact: true }),
  ).toBeEnabled();
  expect(state.projects[0].archived_at).toBeNull();
});

test("failed project archival keeps the project available for retry", async ({
  page,
}) => {
  const state = await mockApi(page, { empty: true, role: "project_manager" });
  state.projects = [
    { id: "project-1", name: "Launch", owner_id: "user-1", can_manage: true },
  ];
  state.failProjectArchive = true;
  await page.goto("/dashboard?project=project-1");
  await page
    .getByRole("button", { name: "Options for Launch", exact: true })
    .click();
  await page
    .getByRole("menuitem", { name: "Archive project", exact: true })
    .click();
  const confirmation = page.getByRole("dialog", { name: "Archive project?" });
  await confirmation.getByRole("button", { name: "Yes", exact: true }).click();
  await expect(confirmation.getByRole("alert")).toContainText(
    "Could not update project",
  );
  await expect(
    page.getByRole("button", { name: "Project Launch", exact: true }),
  ).toBeVisible();
  expect(state.projects[0].archived_at).toBeUndefined();
  state.failProjectArchive = false;
  await confirmation.getByRole("button", { name: "Yes", exact: true }).click();
  await expect(confirmation).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Project Launch", exact: true }),
  ).toHaveCount(0);
});

test("developer can view archived projects without archive or restore controls", async ({
  page,
}) => {
  const state = await mockApi(page);
  state.projects = [
    { id: "project-1", name: "Launch", owner_id: "user-2", can_manage: false },
  ];
  state.tasks[0] = {
    ...state.tasks[0],
    project_id: "project-1",
    assignee_id: state.account.id,
  };
  await page.goto("/dashboard?project=project-1");
  await page
    .getByRole("button", { name: "Options for Launch", exact: true })
    .click();
  await expect(
    page.getByRole("menuitem", { name: "Archive project", exact: true }),
  ).toHaveCount(0);
  state.projects[0].archived_at = new Date().toISOString();
  await page.reload();
  await page
    .getByRole("button", { name: "Show archived projects", exact: true })
    .click();
  await expect(
    page.getByRole("dialog", { name: "Archived projects", exact: true }),
  ).toContainText("Launch");
  await expect(
    page.getByRole("button", { name: "Restore project Launch", exact: true }),
  ).toHaveCount(0);
});

test("project dots menu opens info modal to view, add, and remove members", async ({
  page,
}) => {
  const state = await mockApi(page, { role: "project_manager" });
  state.projects = [
    { id: "project-1", name: "test1", owner_id: "user-1", can_manage: true },
  ];
  state.members["project-1"] = [state.accounts[0], state.accounts[1]];
  state.tasks[0] = {
    ...state.tasks[0],
    project_id: "project-1",
    project_name: "test1",
    assignee_id: "user-2",
    assignee: state.accounts[1],
  };

  await page.goto("/dashboard?project=project-1");

  await expect(page.locator(".project-people")).toHaveCount(0);
  await expect(page.getByTestId("task-card")).toHaveCount(1);
  await expect(page.getByTestId("task-card")).toContainText("@Jamie Developer");

  // Verify the dots button exists next to the project in the sidebar
  const dotsButton = page.getByRole("button", {
    name: "Options for test1",
    exact: true,
  });
  await expect(dotsButton).toBeVisible();

  // Click dots button to reveal dropdown menu
  await dotsButton.click();
  const infoOption = page.getByRole("menuitem", { name: "Info", exact: true });
  await expect(infoOption).toBeVisible();

  await expect(dotsButton).toHaveAttribute("aria-expanded", "true");
  await expect(infoOption).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(infoOption).not.toBeVisible();
  await expect(dotsButton).toBeFocused();
  await dotsButton.click();
  await page.locator(".topbar").click();
  await expect(infoOption).not.toBeVisible();
  await dotsButton.click();

  // Click Info to open the modal
  await infoOption.click();

  // Modal is now visible
  const modal = page.getByRole("dialog");
  await expect(modal).toBeVisible();
  await expect(modal.getByRole("heading", { name: "test1" })).toBeVisible();

  // Verify who is inside (both members)
  await expect(modal.getByText("Alex Morgan", { exact: true })).toBeVisible();
  await expect(modal.getByText("Owner", { exact: true })).toBeVisible();
  await expect(
    modal.getByText("Jamie Developer", { exact: true }),
  ).toBeVisible();

  // Owner cannot be removed, but Jamie Developer has remove button
  const removeJamieBtn = modal.getByRole("button", {
    name: "Remove Jamie Developer from project",
    exact: true,
  });
  await expect(removeJamieBtn).toBeVisible();

  await expect(
    modal.getByRole("button", { name: "Remove Alex Morgan from project" }),
  ).toHaveCount(0);
  await modal.getByRole("button", { name: "Close project info" }).focus();
  await page.keyboard.press("Shift+Tab");
  await expect(
    modal.getByRole("button", { name: "Add person", exact: true }),
  ).not.toBeFocused();
  await expect(modal.getByLabel("Person email", { exact: true })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(
    modal.getByRole("button", { name: "Close project info" }),
  ).toBeFocused();

  // Remove Jamie Developer
  await removeJamieBtn.click();
  await modal.getByRole("button", { name: "Yes", exact: true }).click();
  await expect(
    modal
      .locator(".members-list")
      .getByText("Jamie Developer", { exact: true }),
  ).toHaveCount(0);
  expect(
    state.members["project-1"].some((m) => m.name === "Jamie Developer"),
  ).toBe(false);

  await expect(page.getByTestId("task-card")).not.toContainText(
    "@Jamie Developer",
  );
  expect(state.tasks[0].assignee_id).toBeNull();

  // Add a new person
  state.accounts.push({
    id: "user-3",
    name: "Sam Smith",
    email: "sam@example.com",
    role: "developer",
  });
  await modal.getByPlaceholder("Add by email address…").fill("sam@example.com");
  await modal.getByRole("button", { name: "Add person" }).click();

  await expect(modal.getByText("Sam Smith", { exact: true })).toBeVisible();
  expect(state.members["project-1"].some((m) => m.name === "Sam Smith")).toBe(
    true,
  );

  await page.screenshot({
    path: `../output/dashboard/project-info-${page.viewportSize()!.width < 600 ? "mobile" : "desktop"}.png`,
  });
  await page.keyboard.press("Escape");
  await expect(modal).not.toBeVisible();
  await expect(dotsButton).toBeFocused();

  await dotsButton.click();
  await page.getByRole("menuitem", { name: "Info", exact: true }).click();
  await expect(modal).toBeVisible();
  await page.mouse.click(page.viewportSize()!.width - 4, 4);
  await expect(modal).not.toBeVisible();
  await expect(dotsButton).toBeFocused();
});

test("developer can view project info without member management controls", async ({
  page,
}) => {
  const state = await mockApi(page);
  state.projects = [
    { id: "project-1", name: "Launch", owner_id: "user-2", can_manage: false },
  ];
  state.members["project-1"] = state.accounts;
  state.tasks[0] = {
    ...state.tasks[0],
    project_id: "project-1",
    assignee_id: state.account.id,
  };
  await page.goto("/dashboard?project=project-1");
  await page
    .getByRole("button", { name: "Options for Launch", exact: true })
    .click();
  await page.getByRole("menuitem", { name: "Info", exact: true }).click();
  const modal = page.getByRole("dialog");
  await expect(
    modal.getByText("Jamie Developer", { exact: true }),
  ).toBeVisible();
  await expect(
    modal.getByRole("button", { name: /Remove .* from project/ }),
  ).toHaveCount(0);
  await expect(
    modal.getByRole("button", { name: "Add person", exact: true }),
  ).toHaveCount(0);
});

test("completed tasks can be archived, recovered after reload, and restored", async ({
  page,
}) => {
  const state = await mockApi(page);
  await page.goto("/dashboard");
  await expect(
    page.getByRole("button", {
      name: `Archive ${initial[2].title}`,
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", {
      name: `Archive ${initial[0].title}`,
      exact: true,
    }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", {
      name: `Archive ${initial[1].title}`,
      exact: true,
    }),
  ).toHaveCount(0);
  await page
    .getByRole("combobox", {
      name: `Status for ${initial[0].title}`,
      exact: true,
    })
    .selectOption("completed");
  const archive = page.getByRole("button", {
    name: `Archive ${initial[0].title}`,
    exact: true,
  });
  await expect(archive).toBeEnabled();
  await archive.click();
  const confirmation = page.getByRole("dialog", { name: "Archive task?" });
  await expect(confirmation).toContainText(initial[0].title);
  expect(state.tasks[0].archived_at).toBeUndefined();
  await confirmation.getByRole("button", { name: "No", exact: true }).click();
  await expect(confirmation).toHaveCount(0);
  await expect(archive).toBeFocused();
  expect(state.tasks[0].archived_at).toBeUndefined();
  await archive.click();
  await page.keyboard.press("Escape");
  await expect(confirmation).toHaveCount(0);
  expect(state.tasks[0].archived_at).toBeUndefined();
  state.failArchive = true;
  await archive.click();
  await page.screenshot({
    path: `../output/dashboard/archive-confirmation-${page.viewportSize()!.width < 600 ? "mobile" : "desktop"}.png`,
  });
  await confirmation.getByRole("button", { name: "Yes", exact: true }).click();
  await expect(confirmation.getByRole("alert")).toContainText(
    "Could not archive task",
  );
  await expect(page.getByTestId("task-card")).toHaveCount(3);
  state.failArchive = false;
  await confirmation.getByRole("button", { name: "Yes", exact: true }).click();
  await expect(confirmation).toHaveCount(0);
  await expect(page.getByTestId("task-card")).toHaveCount(2);
  expect(state.tasks[0].archived_at).toBeTruthy();
  expect(state.tasks[0].status).toBe("completed");
  await page.reload();
  await expect(page.getByTestId("task-card")).toHaveCount(2);
  await page
    .getByRole("button", { name: "Show archived tasks", exact: true })
    .click();
  await expect(page.getByTestId("task-card")).toHaveCount(1);
  await expect(
    page.getByRole("combobox", {
      name: `Status for ${initial[0].title}`,
      exact: true,
    }),
  ).toBeDisabled();
  await page
    .getByRole("button", { name: `Open task ${initial[0].title}`, exact: true })
    .click();
  const modal = page.getByRole("dialog");
  await expect(modal).toContainText(initial[0].text);
  await expect(
    modal.getByRole("button", { name: "Save changes", exact: true }),
  ).toHaveCount(0);
  await expect(
    modal.getByLabel("Write a comment", { exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Close task", exact: true }).click();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: `../output/dashboard/archive-${page.viewportSize()!.width < 600 ? "mobile" : "desktop"}.png`,
  });
  await page
    .getByRole("button", { name: `Restore ${initial[0].title}`, exact: true })
    .click();
  await expect(page.getByTestId("task-card")).toHaveCount(0);
  await expect(
    page.getByText("No archived tasks", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Back to board", exact: true })
    .click();
  await expect(page.getByTestId("task-card")).toHaveCount(3);
  expect(state.tasks[0].archived_at).toBeNull();
});

test("assignees can archive completed project tasks and live events remove archived cards", async ({
  page,
}) => {
  const state = await mockApi(page);
  state.projects = [
    { id: "project-1", name: "Launch", owner_id: "user-2", can_manage: false },
  ];
  state.tasks[0] = {
    ...state.tasks[0],
    status: "completed",
    project_id: "project-1",
    assignee_id: state.account.id,
  };
  await page.goto("/dashboard?project=project-1");
  await expect(page.getByTestId("task-card")).toHaveCount(1);
  await expect(page.getByRole("button", { name: /^Archive / })).toBeVisible();
  await expect(page.getByRole("button", { name: /^Delete / })).toHaveCount(0);
  await page.getByRole("button", { name: /^Archive / }).click();
  await page
    .getByRole("dialog", { name: "Archive task?" })
    .getByRole("button", { name: "Yes", exact: true })
    .click();
  await expect(page.getByTestId("task-card")).toHaveCount(0);
  await page
    .getByRole("button", { name: "Show archived tasks", exact: true })
    .click();
  await expect(page.getByRole("button", { name: /^Restore / })).toBeVisible();
  await page.getByRole("button", { name: /^Restore / }).click();
  await page
    .getByRole("button", { name: "Back to board", exact: true })
    .click();
  await expect(page.getByTestId("task-card")).toHaveCount(1);
  state.tasks[0].archived_at = new Date().toISOString();
  await page.evaluate(() =>
    window.taskbotTestStream.dispatchEvent(new MessageEvent("task_changed")),
  );
  await expect(page.getByTestId("task-card")).toHaveCount(0);
});

test("Open Telegram creates a fresh sign-in link and opens it", async ({
  page,
  context,
}) => {
  const state = await mockApi(page);
  await context.route("https://t.me/**", (route) =>
    route.fulfill({ body: "Telegram" }),
  );
  await page.goto("/dashboard");
  expect(state.telegramLinks).toBe(0);
  const popupReady = page.waitForEvent("popup");
  await page
    .getByRole("button", { name: "Open Telegram", exact: true })
    .click();
  const popup = await popupReady;
  await expect(popup).toHaveURL(
    "https://t.me/taskline_test_bot?start=web_test_token",
  );
  expect(state.telegramLinks).toBe(1);
  await expect(page.getByRole("region", { name: "Task board" })).toBeVisible();
});

test("Open Telegram reports failure and allows retry", async ({
  page,
  context,
}) => {
  const state = await mockApi(page);
  state.failTelegramLink = true;
  await context.route("https://t.me/**", (route) =>
    route.fulfill({ body: "Telegram" }),
  );
  await page.goto("/dashboard");
  const failedPopupReady = page.waitForEvent("popup");
  await page
    .getByRole("button", { name: "Open Telegram", exact: true })
    .click();
  const failedPopup = await failedPopupReady;
  await expect(page.getByRole("alert")).toContainText(
    "Telegram is temporarily unavailable.",
  );
  await expect.poll(() => failedPopup.isClosed()).toBe(true);
  state.failTelegramLink = false;
  const popupReady = page.waitForEvent("popup");
  await page
    .getByRole("button", { name: "Open Telegram", exact: true })
    .click();
  await expect(await popupReady).toHaveURL(
    "https://t.me/taskline_test_bot?start=web_test_token",
  );
  expect(state.telegramLinks).toBe(2);
});

test("equal height columns accept a drop beside the bottom of a long column", async ({
  page,
}, info) => {
  test.skip(
    info.project.name !== "desktop",
    "Side-by-side columns are used on desktop",
  );
  const state = await mockApi(page);
  state.tasks = Array.from({ length: 5 }, (_, index) => ({
    ...initial[0],
    id: `long-${index}`,
    title: `Long column task ${index}`,
    text: `Description for task ${index}`,
  }));
  await page.setViewportSize({ width: 1440, height: 2200 });
  await page.goto("/dashboard");
  const pending = page.getByRole("region", { name: "Pending", exact: true });
  const progress = page.getByRole("region", {
    name: "In progress",
    exact: true,
  });
  const completed = page.getByRole("region", {
    name: "Completed",
    exact: true,
  });
  const bounds = await Promise.all([
    pending.boundingBox(),
    progress.boundingBox(),
    completed.boundingBox(),
  ]);
  expect(bounds[0]!.height).toBeGreaterThan(700);
  expect(Math.abs(bounds[0]!.height - bounds[1]!.height)).toBeLessThan(2);
  expect(Math.abs(bounds[0]!.height - bounds[2]!.height)).toBeLessThan(2);
  const handle = page.getByRole("button", {
    name: "Move Long column task 4",
    exact: true,
  });
  const start = (await handle.boundingBox())!;
  const target = bounds[1]!;
  const dropY = start.y + start.height / 2;
  expect(dropY).toBeGreaterThan(target.y + 380);
  await page.mouse.move(start.x + start.width / 2, dropY);
  await page.mouse.down();
  await page.mouse.move(start.x + start.width / 2 + 10, dropY, { steps: 3 });
  await page.mouse.move(target.x + target.width / 2, dropY, { steps: 15 });
  await expect(progress).toHaveClass(/drop-target/);
  await page.mouse.up();
  await expect(
    progress.getByRole("heading", { name: "Long column task 4", exact: true }),
  ).toBeVisible();
  await expect.poll(() => state.tasks[4].status).toBe("in_progress");
  await page.screenshot({
    path: "../output/dashboard/equal-height-columns.png",
    fullPage: true,
  });
});

test("reconnects the live stream with backoff after an HTTP error or reconnect event", async ({
  page,
}) => {
  const state = await mockApi(page);
  await page.goto("/dashboard");
  await expect(page.getByTestId("task-card")).toHaveCount(3);
  expect(await page.evaluate(() => window.taskbotTestStreams)).toBe(1);
  state.tasks.push({
    ...initial[0],
    id: "outage",
    title: "Captured during the API restart",
    text: "Captured during the API restart",
  });
  // e.g. a 502 while the API restarts: EventSource closes for good.
  await page.evaluate(() => window.taskbotTestStream.fail());
  await expect
    .poll(() => page.evaluate(() => window.taskbotTestStreams))
    .toBe(2);
  await expect(
    page.getByText("Captured during the API restart", { exact: true }),
  ).toBeVisible();
  await page.evaluate(() =>
    window.taskbotTestStream.dispatchEvent(new MessageEvent("reconnect")),
  );
  await expect
    .poll(() => page.evaluate(() => window.taskbotTestStreams))
    .toBe(3);
  await expect(page.getByTestId("task-card")).toHaveCount(4);
});

test("an expired session closes the live stream and returns to login", async ({
  page,
}) => {
  const state = await mockApi(page);
  await page.goto("/dashboard");
  await expect(page.getByTestId("task-card")).toHaveCount(3);
  await page.waitForLoadState("networkidle");
  state.signedIn = false;
  await page.evaluate(() => {
    setTimeout(() => window.taskbotTestStream.fail());
  });
  await expect(page).toHaveURL("/login");
  await expect(page.getByLabel("Email", { exact: true })).toBeVisible();
  await expect(page.getByTestId("task-card")).toHaveCount(0);
});

test("a 401 from a background refetch mid-session logs the browser out", async ({
  page,
}) => {
  const state = await mockApi(page);
  await page.goto("/dashboard");
  await expect(page.getByTestId("task-card")).toHaveCount(3);
  await page.waitForLoadState("networkidle");
  state.signedIn = false;
  // Dispatch asynchronously: the resulting navigation would abort evaluate.
  await page.evaluate(() => {
    setTimeout(() =>
      window.taskbotTestStream.dispatchEvent(new MessageEvent("task_changed")),
    );
  });
  await expect(page).toHaveURL("/login");
  await expect(page.getByLabel("Email", { exact: true })).toBeVisible();
  await expect(page.getByTestId("task-card")).toHaveCount(0);
});

test("a 401 from a mutation mid-session logs the browser out", async ({
  page,
}) => {
  const state = await mockApi(page);
  await page.goto("/dashboard");
  await expect(page.getByTestId("task-card")).toHaveCount(3);
  state.signedIn = false;
  await page
    .getByRole("combobox", { name: `Status for ${initial[0].title}` })
    .selectOption("completed");
  await expect(page).toHaveURL("/login");
  await expect(page.getByLabel("Email", { exact: true })).toBeVisible();
});

test("logging out with an already expired session still completes", async ({
  page,
}) => {
  const state = await mockApi(page);
  await page.goto("/dashboard");
  await page.getByRole("button", { name: "Log out", exact: true }).click();
  state.signedIn = false;
  await page
    .getByRole("button", { name: "Log out on this device", exact: true })
    .click();
  await expect(page).toHaveURL("/login");
  await expect(page.getByRole("status")).toHaveText(
    "You have been logged out.",
  );
});

test("typing a comment survives live refetches of the open task", async ({
  page,
}) => {
  const state = await mockApi(page);
  await page.goto("/dashboard");
  await page
    .getByRole("button", { name: `Open task ${initial[0].title}`, exact: true })
    .click();
  const dialog = page.getByRole("dialog");
  const box = dialog.getByLabel("Write a comment", { exact: true });
  await box.click();
  await page.keyboard.type("Hello");
  state.tasks[0].due_at = "2026-12-01T10:00:00Z";
  await page.evaluate(() =>
    window.taskbotTestStream.dispatchEvent(new MessageEvent("task_changed")),
  );
  await expect(dialog.locator(".detail-summary")).not.toContainText(
    "No deadline",
  );
  await page.keyboard.type(" world");
  await expect(box).toBeFocused();
  await expect(box).toHaveValue("Hello world");
});

test("a failed status change rolls back even when the refetch also fails", async ({
  page,
}) => {
  const state = await mockApi(page);
  await page.goto("/dashboard");
  await expect(page.getByTestId("task-card")).toHaveCount(3);
  state.failUpdate = true;
  state.failList = true;
  await page
    .getByRole("combobox", { name: `Status for ${initial[0].title}` })
    .selectOption("completed");
  const card = page
    .getByRole("region", { name: "Pending", exact: true })
    .getByTestId("task-card");
  await expect(card).toContainText(initial[0].title);
  await expect(card.getByRole("alert")).toContainText("Could not save status.");
  await expect(
    page.getByRole("region", { name: "Completed", exact: true }),
  ).not.toContainText(initial[0].title);
  // The last loaded board stays usable while refreshes fail.
  await expect(page.getByText("Could not refresh tasks.")).toBeVisible();
  await expect(page.getByTestId("task-card")).toHaveCount(3);
  expect(state.tasks[0].status).toBe("pending");
});

test("loaded cards stay loaded when live updates change a column", async ({
  page,
}) => {
  const state = await mockApi(page);
  state.tasks = Array.from({ length: 30 }, (_, index) => ({
    ...initial[0],
    id: `paged-${index}`,
    title: `Paged task ${index}`,
    text: `Paged task ${index}`,
  }));
  await page.goto("/dashboard");
  const pending = page
    .getByRole("region", { name: "Pending", exact: true })
    .getByTestId("task-card");
  await expect(pending).toHaveCount(25);
  const more = page.getByRole("button", { name: /^Load more/ });
  await expect(async () => {
    if (await more.isVisible())
      await more.click({ timeout: 1000 }).catch(() => {});
    await expect(pending).toHaveCount(30, { timeout: 1000 });
  }).toPass();
  state.tasks.push({
    ...initial[0],
    id: "paged-new",
    title: "Arrived live",
    text: "Arrived live",
  });
  await page.evaluate(() =>
    window.taskbotTestStream.dispatchEvent(new MessageEvent("task_changed")),
  );
  await expect(pending).toHaveCount(31);
});

test("stacked mobile columns load more only on request", async ({
  page,
}, info) => {
  test.skip(info.project.name !== "mobile", "Columns stack on narrow screens");
  const state = await mockApi(page);
  state.tasks = [
    ...Array.from({ length: 30 }, (_, index) => ({
      ...initial[0],
      id: `stacked-${index}`,
      title: `Stacked task ${index}`,
      text: `Stacked task ${index}`,
    })),
    initial[1],
  ];
  await page.goto("/dashboard");
  const pending = page
    .getByRole("region", { name: "Pending", exact: true })
    .getByTestId("task-card");
  await expect(pending).toHaveCount(25);
  const progress = page.getByRole("region", {
    name: "In progress",
    exact: true,
  });
  await progress.getByText(initial[1].title).scrollIntoViewIfNeeded();
  await expect(progress.getByText(initial[1].title)).toBeInViewport();
  // Give a (wrongly) attached observer time to fire.
  await page.waitForTimeout(300);
  await expect(pending).toHaveCount(25);
  await page.getByRole("button", { name: "Load more (5 remaining)" }).click();
  await expect(pending).toHaveCount(30);
});

test("loads every page of a large task list", async ({ page }) => {
  const state = await mockApi(page);
  state.tasks = Array.from({ length: 1100 }, (_, index) => ({
    ...initial[0],
    id: `bulk-${index}`,
    title: `Bulk task ${index}`,
    text: `Bulk task ${index}`,
  }));
  await page.goto("/dashboard");
  await expect(
    page
      .getByRole("button", { name: "My tasks", exact: true })
      .locator(".nav-count"),
  ).toHaveText("1100");
  await expect(
    page.getByRole("region", { name: "Task overview" }),
  ).toContainText("1100");
  for (const offset of [0, 500, 1000]) {
    expect(
      state.taskRequests.some(
        (search) =>
          search.includes("limit=500") && search.includes(`offset=${offset}`),
      ),
    ).toBe(true);
  }
  expect(
    state.taskRequests.some((search) => search.includes("offset=1500")),
  ).toBe(false);
});

test("saving task details sends only changed fields and flags concurrent edits", async ({
  page,
}) => {
  const state = await mockApi(page);
  state.tasks[0].due_at = "2026-12-01T10:00:30.500Z";
  await page.goto("/dashboard");
  await page
    .getByRole("button", { name: `Open task ${initial[0].title}`, exact: true })
    .click();
  const dialog = page.getByRole("dialog");
  await expect(
    dialog.getByRole("button", { name: "Save changes", exact: true }),
  ).toBeDisabled();
  await dialog.getByLabel("Task title", { exact: true }).fill("My new title");
  state.tasks[0].text = "Edited by a teammate";
  state.tasks[0].updated_at = new Date().toISOString();
  await page.evaluate(() =>
    window.taskbotTestStream.dispatchEvent(new MessageEvent("task_changed")),
  );
  await expect(dialog.getByRole("status")).toContainText(
    "updated elsewhere while you had it open",
  );
  await expect(dialog.getByLabel("Task title", { exact: true })).toHaveValue(
    "My new title",
  );
  await dialog
    .getByRole("button", { name: "Save changes", exact: true })
    .click();
  await expect(
    dialog.getByText("Changes saved.", { exact: true }),
  ).toBeVisible();
  expect(state.patches.at(-1)).toEqual({ title: "My new title" });
  expect(state.tasks[0].text).toBe("Edited by a teammate");
  expect(state.tasks[0].due_at).toBe("2026-12-01T10:00:30.500Z");
  await expect(
    dialog.getByText("updated elsewhere while you had it open"),
  ).toHaveCount(0);
});

test("developers' Due soon board and count only include their own tasks", async ({
  page,
}) => {
  const state = await mockApi(page);
  const soon = new Date(Date.now() + 86400000).toISOString();
  state.projects = [
    { id: "project-1", name: "Launch", owner_id: "user-2", can_manage: false },
  ];
  state.tasks = [
    { ...initial[0], due_at: soon },
    {
      ...initial[1],
      due_at: soon,
      project_id: "project-1",
      project_name: "Launch",
      assignee_ids: ["user-2"],
    },
  ];
  await page.goto("/dashboard?view=due");
  await expect(page.getByTestId("task-card")).toHaveCount(1);
  await expect(page.getByTestId("task-card")).toContainText(initial[0].title);
  await expect(
    page
      .getByRole("button", { name: "Due soon", exact: true })
      .locator(".nav-count"),
  ).toHaveText("1");
  expect(
    state.taskRequests.some(
      (search) =>
        search.includes("due_soon=true") && search.includes("mine=true"),
    ),
  ).toBe(true);
});

test("keyboard moves keep focus on the moved card's handle", async ({
  page,
}) => {
  const state = await mockApi(page);
  await page.setViewportSize({
    width: page.viewportSize()!.width,
    height: 2200,
  });
  await page.goto("/dashboard");
  const handle = page.getByRole("button", {
    name: `Move ${initial[0].text}`,
    exact: true,
  });
  await handle.focus();
  await page.keyboard.press("Space");
  await expect(handle).toHaveAttribute("aria-pressed", "true");
  await page.keyboard.press("ArrowRight");
  const target = page.getByRole("region", { name: "In progress", exact: true });
  await expect(target).toHaveClass(/drop-target/);
  await page.keyboard.press("Space");
  await expect(target.getByText(initial[0].text)).toBeVisible();
  await expect.poll(() => state.tasks[0].status).toBe("in_progress");
  await expect(handle).toBeEnabled();
  await expect(handle).toBeFocused();
});

test("unknown project links fall back to My Tasks", async ({ page }) => {
  const state = await mockApi(page);
  await page.goto("/dashboard?project=not-a-project%26mine%3Dfalse");
  await expect(page.getByRole("heading", { name: /^My Tasks/ })).toBeVisible();
  await expect(page).toHaveURL("/dashboard");
  await expect(page.getByTestId("task-card")).toHaveCount(3);
  expect(
    state.taskRequests.some((search) => search.includes("not-a-project")),
  ).toBe(false);
});

test("long project names and help stay usable on small screens", async ({
  page,
}) => {
  const state = await mockApi(page);
  const name = "A".repeat(120);
  state.projects = [
    { id: "project-1", name, owner_id: "user-2", can_manage: false },
  ];
  state.tasks[0] = {
    ...state.tasks[0],
    project_id: "project-1",
    project_name: name,
    assignee_ids: ["user-1"],
  };
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/dashboard?project=project-1");
  await expect(page.getByTestId("task-card")).toHaveCount(1);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.getByRole("button", { name: "How it works" }).click();
  const help = page.getByRole("dialog");
  await expect(help).toBeVisible();
  await expect(help.getByRole("button", { name: /Got it/ })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(help).toHaveCount(0);
});

test("developers without manageable projects get no project picker", async ({
  page,
}) => {
  const state = await mockApi(page);
  state.projects = [
    { id: "project-1", name: "Launch", owner_id: "user-2", can_manage: false },
  ];
  state.tasks[0] = {
    ...state.tasks[0],
    project_id: "project-1",
    assignee_ids: ["user-1"],
  };
  await page.goto("/dashboard");
  await page.getByRole("button", { name: "Add task", exact: true }).click();
  await expect(page.getByLabel("Task title", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Task project", { exact: true })).toHaveCount(0);
});
