import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  type Project,
  type Status,
  type Task,
  type User,
} from "../../api";
import { AssigneeSelector } from "../../components/AssigneeSelector";
import { columns } from "./columns";

export function TaskComposer({
  onClose,
  projects,
  initialProject,
}: {
  onClose: () => void;
  projects: Project[];
  initialProject: string | null;
}) {
  const client = useQueryClient();
  const manageable = projects.filter((project) => project.can_manage);
  const [text, setText] = useState("");
  const [title, setTitle] = useState("");
  const [status, setStatus] = useState<Status>("pending");
  const [projectId, setProjectId] = useState(initialProject ?? "");
  const [assigneeIds, setAssigneeIds] = useState<string[]>([]);
  const [due, setDue] = useState("");
  const members = useQuery({
    queryKey: ["members", projectId],
    queryFn: () => api<User[]>(`/projects/${projectId}/members`),
    enabled: !!projectId,
  });
  const creation = useMutation({
    mutationFn: () =>
      api<Task>("/tasks", {
        method: "POST",
        body: JSON.stringify({
          text,
          title,
          status,
          project_id: projectId || null,
          assignee_ids: assigneeIds,
          due_at: due ? new Date(due).toISOString() : null,
        }),
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["tasks"], exact: false });
      void client.invalidateQueries({ queryKey: ["projects"] });
      onClose();
    },
  });
  return (
    <form
      className="task-composer"
      aria-label="New task"
      onSubmit={(event) => {
        event.preventDefault();
        if (title.trim() && text.trim()) creation.mutate();
      }}
    >
      <label htmlFor="new-task-title">Task title</label>
      <input
        id="new-task-title"
        placeholder="A short headline…"
        value={title}
        onChange={(event) => setTitle(event.target.value)}
        maxLength={120}
        required
        autoFocus
        disabled={creation.isPending}
      />
      <label htmlFor="new-task-text">Task description</label>
      <textarea
        id="new-task-text"
        placeholder="Add the details…"
        value={text}
        onChange={(event) => setText(event.target.value)}
        maxLength={10000}
        required
        rows={3}
        disabled={creation.isPending}
      />
      <div className="task-fields">
        {manageable.length > 0 && (
          <label>
            Project
            <select
              aria-label="Task project"
              value={projectId}
              onChange={(event) => {
                setProjectId(event.target.value);
                setAssigneeIds([]);
              }}
            >
              <option value="">Personal task</option>
              {manageable.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.name}
                </option>
              ))}
            </select>
          </label>
        )}
        {projectId && (
          <AssigneeSelector
            key={projectId}
            label="Assign task to"
            people={members.data ?? []}
            value={assigneeIds}
            onChange={setAssigneeIds}
            loading={members.isPending}
            error={members.error?.message}
            disabled={creation.isPending}
          />
        )}
        <label>
          Deadline
          <input
            aria-label="Task deadline"
            type="datetime-local"
            value={due}
            onChange={(event) => setDue(event.target.value)}
          />
        </label>
      </div>
      <div className="composer-actions">
        <label>
          Status
          <select
            aria-label="New task status"
            value={status}
            onChange={(event) => setStatus(event.target.value as Status)}
            disabled={creation.isPending}
          >
            {columns.map((column) => (
              <option key={column.status} value={column.status}>
                {column.label}
              </option>
            ))}
          </select>
        </label>
        <div>
          <button
            className="text-button"
            type="button"
            disabled={creation.isPending}
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            className="primary-button"
            type="submit"
            disabled={!title.trim() || !text.trim() || creation.isPending}
          >
            {creation.isPending ? "Adding…" : "Create task"}
          </button>
        </div>
      </div>
      {creation.error && (
        <p className="card-error" role="alert">
          {creation.error.message}
        </p>
      )}
    </form>
  );
}
