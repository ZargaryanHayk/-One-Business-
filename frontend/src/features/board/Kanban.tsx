import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  MouseSensor,
  TouchSensor,
  closestCenter,
  pointerWithin,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type CollisionDetection,
  type KeyboardCoordinateGetter,
} from "@dnd-kit/core";
import { GripVertical } from "lucide-react";
import type { Status, Task } from "../../api";
import { useTaskStatus } from "../../taskStatus";

const statuses: Status[] = ["pending", "in_progress", "completed"];
// A keyboard move remounts the card in its new column; remember which handle
// should receive focus again once the card is interactive.
let focusAfterMove: string | null = null;
const label = (status: unknown) => String(status).replaceAll("_", " ");
const collisionDetection: CollisionDetection = (args) =>
  args.pointerCoordinates ? pointerWithin(args) : closestCenter(args);

const columnCoordinates: KeyboardCoordinateGetter = (
  event,
  { currentCoordinates, context },
) => {
  const directions: Record<string, number> = {
    ArrowLeft: -1,
    ArrowUp: -1,
    ArrowRight: 1,
    ArrowDown: 1,
  };
  const direction = directions[event.code];
  if (!direction) return;
  event.preventDefault();
  const current = context.over?.id ?? context.active?.data.current?.status;
  const target = statuses[statuses.indexOf(current as Status) + direction];
  const rect = target ? context.droppableRects.get(target) : undefined;
  if (!rect || !context.collisionRect) return;
  return {
    x:
      currentCoordinates.x +
      rect.left +
      rect.width / 2 -
      context.collisionRect.left -
      context.collisionRect.width / 2,
    y:
      currentCoordinates.y +
      rect.top +
      rect.height / 2 -
      context.collisionRect.top -
      context.collisionRect.height / 2,
  };
};

export function Kanban({
  tasks,
  children,
}: {
  tasks: Task[];
  children: ReactNode;
}) {
  const [activeTask, setActiveTask] = useState<Task | null>(null);
  const changeStatus = useTaskStatus();
  const sensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: { distance: 6 } }),
    useSensor(TouchSensor, {
      activationConstraint: { delay: 180, tolerance: 6 },
    }),
    useSensor(KeyboardSensor, { coordinateGetter: columnCoordinates }),
  );
  return (
    <DndContext
      sensors={sensors}
      collisionDetection={collisionDetection}
      accessibility={{
        screenReaderInstructions: {
          draggable:
            "Press Space to pick up a task. Use arrow keys to choose a column, Space to drop, or Escape to cancel.",
        },
        announcements: {
          onDragStart: () => "Task picked up. Choose a column.",
          onDragOver: ({ over }) =>
            over ? `Over ${label(over.id)}.` : "Outside the board.",
          onDragEnd: ({ over }) =>
            over
              ? `Dropped in ${label(over.id)}. Saving task status.`
              : "Move cancelled.",
          onDragCancel: () => "Move cancelled.",
        },
      }}
      onDragStart={({ active }) =>
        setActiveTask(tasks.find((task) => task.id === active.id) ?? null)
      }
      onDragCancel={() => setActiveTask(null)}
      onDragEnd={({ active, over, activatorEvent }) => {
        setActiveTask(null);
        const task = tasks.find((item) => item.id === active.id);
        const status = over?.id as Status | undefined;
        if (
          task &&
          status &&
          statuses.includes(status) &&
          task.status !== status
        ) {
          const restoreFocus = activatorEvent instanceof KeyboardEvent;
          if (restoreFocus) focusAfterMove = task.id;
          changeStatus.mutate(
            { id: task.id, status },
            {
              onSettled: () => {
                if (!restoreFocus) return;
                // Give the re-enabled card a moment to render, then forget.
                window.setTimeout(() => {
                  if (focusAfterMove === task.id) focusAfterMove = null;
                }, 1000);
              },
            },
          );
        }
      }}
    >
      <div className="kanban">{children}</div>
      <DragOverlay dropAnimation={null}>
        {activeTask && (
          <div className="task-card drag-overlay" aria-hidden="true">
            <div className="drag-preview-heading">
              <GripVertical size={16} /> Moving task
            </div>
            <h3 className="task-title">{activeTask.title}</h3>
            {activeTask.text !== activeTask.title && (
              <p className="task-text">{activeTask.text}</p>
            )}
          </div>
        )}
      </DragOverlay>
    </DndContext>
  );
}

export function KanbanColumn({
  status,
  name,
  children,
}: {
  status: Status;
  name: string;
  children: ReactNode;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: status });
  return (
    <section
      ref={setNodeRef}
      className={`column ${status}${isOver ? " drop-target" : ""}`}
      aria-label={name}
    >
      {children}
    </section>
  );
}

export function DraggableTask({
  task,
  disabled,
  children,
}: {
  task: Task;
  disabled: boolean;
  children: ReactNode;
}) {
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, isDragging } =
    useDraggable({
      id: task.id,
      data: { status: task.status },
      disabled,
    });
  const handle = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    if (!disabled && focusAfterMove === task.id) {
      focusAfterMove = null;
      handle.current?.focus();
    }
  }, [disabled, task.id]);
  return (
    <article
      ref={setNodeRef}
      className={`task-card ${task.status === "completed" ? "is-done" : ""}${isDragging ? " is-dragging" : ""}`}
      data-testid="task-card"
      onMouseDown={(event) => {
        if (
          !(event.target as HTMLElement).closest(
            "button, a, input, select, textarea",
          )
        ) {
          listeners?.onMouseDown?.(event);
        }
      }}
    >
      <button
        ref={(node) => {
          setActivatorNodeRef(node);
          handle.current = node;
        }}
        className="drag-handle"
        {...attributes}
        {...listeners}
        aria-label={`Move ${task.title}`}
        title="Drag to another column, or press Space and use arrow keys"
        disabled={disabled}
      >
        <GripVertical size={16} />
      </button>
      {children}
    </article>
  );
}
