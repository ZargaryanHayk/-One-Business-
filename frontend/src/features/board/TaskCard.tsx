import { useState } from "react";
import {
  useMutation,
  useMutationState,
  useQueryClient,
} from "@tanstack/react-query";
import {
  Archive,
  ArchiveRestore,
  CalendarClock,
  Check,
  CircleCheck,
  FolderKanban,
  LoaderCircle,
  MessageSquare,
  Trash2,
} from "lucide-react";
import { api, taskAssignees, type Status, type Task } from "../../api";
import { ArchiveConfirmationDialog } from "../../components/ArchiveConfirmationDialog";
import { deadlineText, isDueSoon } from "../../dates";
import { useTaskStatus } from "../../taskStatus";
import { columns } from "./columns";
import { DraggableTask } from "./Kanban";

export function TaskCard({
  task,
  onOpen,
  isDueView,
}: {
  task: Task;
  onOpen: () => void;
  isDueView?: boolean;
}) {
  const client = useQueryClient();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [confirmArchive, setConfirmArchive] = useState(false);
  const removal = useMutation({
    mutationFn: () => api(`/tasks/${task.id}`, { method: "DELETE" }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["tasks"], exact: false });
    },
  });
  const archival = useMutation({
    mutationFn: () =>
      api<Task>(`/tasks/${task.id}`, {
        method: "PATCH",
        body: JSON.stringify({ archived: !task.archived_at }),
      }),
    onSuccess: async () => {
      setConfirmArchive(false);
      await Promise.all([
        client.invalidateQueries({ queryKey: ["tasks"], exact: false }),
        client.invalidateQueries({ queryKey: ["projects"] }),
      ]);
    },
  });
  const history = useMutationState({
    filters: { mutationKey: ["status"] },
    select: (mutation) => ({
      id: (mutation.state.variables as { id: string } | undefined)?.id,
      status: mutation.state.status,
      error: mutation.state.error,
      submittedAt: mutation.state.submittedAt,
    }),
  });
  const latest = history.filter((item) => item.id === task.id).at(-1);
  const mutation = useTaskStatus();
  // A failed status save stays visible until the task changes on the server
  // (or a later status change for it succeeds), not for the mutation's lifetime.
  const [statusError, setStatusError] = useState<{
    submittedAt: number;
    updatedAt: string;
  } | null>(null);
  if (
    latest?.status === "error" &&
    statusError?.submittedAt !== latest.submittedAt
  ) {
    setStatusError({
      submittedAt: latest.submittedAt,
      updatedAt: task.updated_at,
    });
  }
  const showStatusError =
    latest?.status === "error" &&
    statusError?.submittedAt === latest.submittedAt &&
    statusError.updatedAt === task.updated_at;

  return (
    <DraggableTask
      task={task}
      disabled={
        latest?.status === "pending" ||
        removal.isPending ||
        archival.isPending ||
        !!task.archived_at ||
        confirmDelete ||
        confirmArchive ||
        task.can_move === false
      }
    >
      <div className="card-top">
        <span className="task-project" title={task.project_name || "Personal"}>
          <FolderKanban size={12} />
          <span className="task-project-name">
            {task.project_name || "Personal"}
          </span>
        </span>
        {task.status === "completed" && (
          <span className="done-mark">
            <Check size={14} />
          </span>
        )}
      </div>
      <h3 className="task-title">{task.title}</h3>
      {task.text !== task.title && <p className="task-text">{task.text}</p>}
      {(taskAssignees(task).length > 0 || task.due_at) && (
        <div className="task-metadata">
          {taskAssignees(task).map((person) => (
            <span key={person.id} title={person.email}>
              @{person.name}
            </span>
          ))}
          {task.due_at && (
            <span className={isDueSoon(task) ? "deadline-urgent" : ""}>
              <CalendarClock size={12} />
              {deadlineText(task.due_at)}
              {task.status !== "completed" &&
              new Date(task.due_at).getTime() < Date.now()
                ? " · Overdue"
                : ""}
            </span>
          )}
        </div>
      )}

      <div className="card-footer">
        <time
          dateTime={task.created_at}
          title={new Date(task.created_at).toLocaleString()}
        >
          {new Date(task.created_at).toLocaleDateString(undefined, {
            month: "short",
            day: "numeric",
          })}
        </time>
        <div className="select-wrap">
          {latest?.status === "pending" && (
            <LoaderCircle className="spin" size={12} />
          )}
          <select
            aria-label={`Status for ${task.title}`}
            value={task.status}
            disabled={
              latest?.status === "pending" ||
              removal.isPending ||
              archival.isPending ||
              !!task.archived_at ||
              task.can_move === false
            }
            onChange={(e) =>
              mutation.mutate({ id: task.id, status: e.target.value as Status })
            }
          >
            {columns.map((c) => (
              <option key={c.status} value={c.status}>
                {isDueView && c.status === "completed"
                  ? "Move to Completed"
                  : c.label}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div className="card-actions">
        <button
          className="task-open-button"
          aria-label={`Open task ${task.title}`}
          onClick={onOpen}
        >
          <MessageSquare size={12} /> Details & comments
        </button>
        {task.status === "completed" &&
          (task.can_archive ?? task.can_manage !== false) && (
            <button
              type="button"
              className="archive-button"
              aria-label={`${task.archived_at ? "Restore" : "Archive"} ${task.title}`}
              title={task.archived_at ? "Restore to Completed" : "Archive task"}
              disabled={
                archival.isPending ||
                removal.isPending ||
                confirmDelete ||
                latest?.status === "pending"
              }
              onClick={() => {
                if (task.archived_at) archival.mutate();
                else {
                  archival.reset();
                  setConfirmArchive(true);
                }
              }}
            >
              {task.archived_at ? (
                <ArchiveRestore size={12} />
              ) : (
                <Archive size={12} />
              )}
              {archival.isPending
                ? task.archived_at
                  ? "Restoring…"
                  : "Archiving…"
                : task.archived_at
                  ? "Restore"
                  : "Archive"}
            </button>
          )}
        {isDueView && task.status !== "completed" && (
          <button
            type="button"
            className="complete-button"
            aria-label={`Move ${task.title} to completed`}
            disabled={
              latest?.status === "pending" ||
              removal.isPending ||
              task.can_move === false
            }
            onClick={() =>
              mutation.mutate({ id: task.id, status: "completed" })
            }
          >
            <CircleCheck size={12} /> Move to Completed
          </button>
        )}
        {task.can_manage !== false &&
          (confirmDelete ? (
            <div className="delete-confirmation">
              <span>Delete this task?</span>
              <button
                className="delete-button"
                disabled={removal.isPending || latest?.status === "pending"}
                onClick={() => removal.mutate()}
              >
                {removal.isPending ? "Deleting…" : "Delete task"}
              </button>
              <button
                className="text-button"
                disabled={removal.isPending}
                onClick={() => {
                  setConfirmDelete(false);
                  removal.reset();
                }}
              >
                Keep task
              </button>
            </div>
          ) : (
            <button
              className="delete-button"
              aria-label={`Delete ${task.title}`}
              disabled={latest?.status === "pending" || archival.isPending}
              onClick={() => setConfirmDelete(true)}
            >
              <Trash2 size={12} /> Delete
            </button>
          ))}
      </div>
      {removal.error && (
        <p className="card-error" role="alert">
          {removal.error.message}
        </p>
      )}
      {confirmArchive && (
        <ArchiveConfirmationDialog
          kind="task"
          name={task.title}
          pending={archival.isPending}
          error={archival.error?.message}
          onConfirm={() => archival.mutate()}
          onClose={() => {
            setConfirmArchive(false);
            archival.reset();
          }}
        />
      )}
      {archival.error && !confirmArchive && (
        <p className="card-error" role="alert">
          {archival.error.message}
        </p>
      )}
      {showStatusError && (
        <p className="card-error" role="alert">
          {latest?.error?.message}
        </p>
      )}
    </DraggableTask>
  );
}
