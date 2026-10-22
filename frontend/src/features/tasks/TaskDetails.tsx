import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, UserRound, X } from "lucide-react";
import {
  api,
  taskAssigneeIds,
  taskAssignees,
  type Task,
  type User,
} from "../../api";
import { AssigneeSelector } from "../../components/AssigneeSelector";
import { deadlineText, localDateInput } from "../../dates";
import { TaskComments } from "./TaskComments";

type TaskForm = {
  title: string;
  text: string;
  assignees: string[];
  due: string;
};

function sameIds(left: string[], right: string[]) {
  return left.length === right.length && left.every((id) => right.includes(id));
}

export function TaskDetails({
  task,
  onClose,
}: {
  task: Task;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const panel = useRef<HTMLElement>(null);
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });
  const [text, setText] = useState(task.text);
  const [title, setTitle] = useState(task.title);
  const [assignees, setAssignees] = useState<string[]>(taskAssigneeIds(task));
  const [due, setDue] = useState(localDateInput(task.due_at));
  // Values the form was opened (or last saved) with; only fields that differ
  // from these are sent so concurrent edits by others are not overwritten.
  const [baseline, setBaseline] = useState(() => ({
    title: task.title,
    text: task.text,
    assignees: taskAssigneeIds(task),
    // Minute precision, as shown by datetime-local; seconds are never resent.
    due: localDateInput(task.due_at),
    updatedAt: task.updated_at,
  }));
  const changes: Record<string, unknown> = {};
  if (title !== baseline.title) changes.title = title;
  if (text !== baseline.text) changes.text = text;
  if (!sameIds(assignees, baseline.assignees)) changes.assignee_ids = assignees;
  if (due !== baseline.due)
    changes.due_at = due ? new Date(due).toISOString() : null;
  const dirty = Object.keys(changes).length > 0;
  const changedElsewhere =
    Date.parse(task.updated_at) > Date.parse(baseline.updatedAt);
  const loadLatest = () => {
    const latest = {
      title: task.title,
      text: task.text,
      assignees: taskAssigneeIds(task),
      due: localDateInput(task.due_at),
    };
    setTitle(latest.title);
    setText(latest.text);
    setAssignees(latest.assignees);
    setDue(latest.due);
    setBaseline({ ...latest, updatedAt: task.updated_at });
  };
  const people = useQuery({
    queryKey: ["members", task.project_id],
    queryFn: () => api<User[]>(`/projects/${task.project_id}/members`),
    enabled: !!task.project_id && task.can_manage !== false,
  });
  const save = useMutation({
    mutationFn: ({ body }: { body: Record<string, unknown>; form: TaskForm }) =>
      api<Task>(`/tasks/${task.id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    onSuccess: (updated, { form }) => {
      setBaseline({ ...form, updatedAt: updated.updated_at });
      void client.invalidateQueries({ queryKey: ["tasks"], exact: false });
      void client.invalidateQueries({ queryKey: ["projects"] });
    },
  });
  // Runs once per open dialog: re-renders (live updates, inline onClose
  // props) must not move focus away from what the user is typing in.
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    panel.current?.querySelector<HTMLButtonElement>("button")?.focus();
    const listener = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCloseRef.current();
      if (event.key === "Tab") {
        const elements = panel.current?.querySelectorAll<HTMLElement>(
          "button:not(:disabled), input, select, textarea",
        );
        if (!elements?.length) return;
        const first = elements[0],
          last = elements[elements.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", listener);
    return () => {
      document.removeEventListener("keydown", listener);
      previous?.focus();
    };
  }, []);
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <section
        ref={panel}
        className="task-details"
        role="dialog"
        aria-modal="true"
        aria-labelledby="task-details-title"
        onClick={(event) => event.stopPropagation()}
      >
        <button
          className="icon-button close-modal"
          aria-label="Close task"
          onClick={onClose}
        >
          <X size={19} />
        </button>
        <span className="eyebrow">
          {task.project_name || "PERSONAL WORKSPACE"}
        </span>
        <h2 id="task-details-title">{task.title}</h2>
        <div className="detail-summary">
          <span>
            <UserRound size={15} />{" "}
            {taskAssignees(task)
              .map((person) => person.name)
              .join(", ") || "Unassigned"}
          </span>
          <span>
            <CalendarClock size={15} />{" "}
            {task.due_at ? deadlineText(task.due_at) : "No deadline"}
          </span>
        </div>
        {task.can_manage !== false && !task.archived_at ? (
          <form
            className="detail-form"
            onSubmit={(event) => {
              event.preventDefault();
              if (dirty)
                save.mutate({
                  body: changes,
                  form: { title, text, assignees, due },
                });
            }}
          >
            {changedElsewhere && (
              <div className="notice detail-conflict" role="status">
                <span>
                  This task was updated elsewhere while you had it open. Saving
                  only sends the fields you changed.
                </span>
                <button
                  type="button"
                  className="text-button"
                  onClick={loadLatest}
                >
                  Load latest
                </button>
              </div>
            )}
            <label htmlFor="edit-task-title">Task title</label>
            <input
              id="edit-task-title"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              required
              maxLength={120}
            />
            <label htmlFor="edit-task-text">Task description</label>
            <textarea
              id="edit-task-text"
              value={text}
              onChange={(event) => setText(event.target.value)}
              required
              maxLength={10000}
              rows={4}
            />
            {task.project_id && (
              <AssigneeSelector
                label="Edit assignee"
                people={people.data ?? []}
                value={assignees}
                onChange={setAssignees}
                loading={people.isPending}
                error={people.error?.message}
                disabled={save.isPending}
              />
            )}
            <label>
              Deadline
              <input
                aria-label="Edit deadline"
                type="datetime-local"
                value={due}
                onChange={(event) => setDue(event.target.value)}
              />
            </label>
            <button
              className="primary-button"
              disabled={
                !dirty || !title.trim() || !text.trim() || save.isPending
              }
            >
              {save.isPending ? "Saving…" : "Save changes"}
            </button>
            {save.error && (
              <p className="card-error" role="alert">
                {save.error.message}
              </p>
            )}
            {save.isSuccess && !dirty && (
              <p className="saved-message" role="status">
                Changes saved.
              </p>
            )}
          </form>
        ) : (
          <div>
            <h3 className="detail-description-heading">Description</h3>
            <p className="detail-text">{task.text}</p>
          </div>
        )}
        <TaskComments task={task} />
      </section>
    </div>
  );
}
