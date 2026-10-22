import type { Task } from "./api";

export function localDateInput(value?: string | null) {
  if (!value) return "";
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
}

export function deadlineText(value: string) {
  return new Date(value).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// Open tasks due within four days (or already overdue) count as due soon.
export function isDueSoon(task: Pick<Task, "status" | "due_at">) {
  return (
    task.status !== "completed" &&
    !!task.due_at &&
    new Date(task.due_at).getTime() <= Date.now() + 4 * 86400000
  );
}
