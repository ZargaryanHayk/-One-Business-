export type Status = "pending" | "in_progress" | "completed";
export type Task = {
  id: string;
  title: string;
  text: string;
  source: "text" | "voice" | "dashboard";
  status: Status;
  created_at: string;
  updated_at: string;
  archived_at?: string | null;
  user_id?: string;
  project_id?: string | null;
  project_name?: string | null;
  project_archived?: boolean;
  assignee_id?: string | null;
  assignee?: User | null;
  assignee_ids?: string[];
  assignees?: User[];
  due_at?: string | null;
  can_manage?: boolean;
  can_move?: boolean;
  can_archive?: boolean;
};
export type User = {
  id: string;
  name: string;
  email?: string;
  role?: "admin" | "project_manager" | "developer";
};

export function taskAssignees(task: Task): User[] {
  return task.assignees ?? (task.assignee ? [task.assignee] : []);
}

export function taskAssigneeIds(task: Task): string[] {
  return task.assignee_ids ?? (task.assignee_id ? [task.assignee_id] : []);
}
export type Project = {
  id: string;
  name: string;
  owner_id: string;
  can_manage: boolean;
  archived_at?: string | null;
};
export type TaskComment = {
  id: string;
  text: string;
  author: User;
  created_at: string;
};
export type PublicConfig = { telegram_bot_url: string | null };

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

function fallbackMessage(status: number) {
  if (status === 413)
    return "That is too large to send. Try something smaller.";
  if (status === 502 || status === 503 || status === 504)
    return "The server is temporarily unavailable. Please try again in a moment.";
  if (status >= 500)
    return "Something went wrong on the server. Please try again.";
  return "Unable to complete the request. Please try again.";
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      ...init,
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch (error) {
    // Let TanStack Query cancellations propagate unchanged.
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    throw new ApiError("Can't reach the server. Check your connection.", 0);
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(
      body?.error?.message ?? fallbackMessage(response.status),
      response.status,
    );
  }
  return body as T;
}

export const TASK_PAGE_SIZE = 500;

/** Fetch every page of a task list; the API caps each response. */
export async function fetchAllTasks(
  params: string,
  signal?: AbortSignal,
): Promise<Task[]> {
  const tasks: Task[] = [];
  const seen = new Set<string>();
  for (let offset = 0; ; offset += TASK_PAGE_SIZE) {
    const query = new URLSearchParams(params);
    query.set("limit", String(TASK_PAGE_SIZE));
    query.set("offset", String(offset));
    const page = await api<Task[]>(`/tasks?${query}`, { signal });
    // Newer tasks can shift items across page boundaries; keep the first copy.
    for (const task of page) {
      if (!seen.has(task.id)) {
        seen.add(task.id);
        tasks.push(task);
      }
    }
    if (page.length < TASK_PAGE_SIZE) return tasks;
  }
}
