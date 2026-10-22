import { useRef } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import {
  Archive,
  CircleCheck,
  CircleHelp,
  LayoutGrid,
  LoaderCircle,
  Plus,
  Search,
  X,
} from "lucide-react";
import type { Task } from "../../api";
import { CardList } from "./CardList";
import { columns } from "./columns";
import { Kanban, KanbanColumn } from "./Kanban";

export function TaskBoard({
  tasks,
  selection,
  viewName,
  canAddTasks,
  showArchived,
  search,
  showSearch,
  onSearch,
  onShowSearch,
  onToggleArchived,
  onAddTask,
  onShowHelp,
  onOpenTask,
}: {
  tasks: UseQueryResult<Task[]>;
  selection: string;
  viewName: string;
  canAddTasks: boolean;
  showArchived: boolean;
  search: string;
  showSearch: boolean;
  onSearch: (value: string) => void;
  onShowSearch: (visible: boolean) => void;
  onToggleArchived: () => void;
  onAddTask: () => void;
  onShowHelp: () => void;
  onOpenTask: (id: string) => void;
}) {
  const searchToggle = useRef<HTMLButtonElement>(null);
  const closeSearch = () => {
    onSearch("");
    onShowSearch(false);
    searchToggle.current?.focus();
  };
  const visible = (tasks.data ?? []).filter((task) =>
    `${task.title} ${task.text}`
      .toLocaleLowerCase()
      .includes(search.toLocaleLowerCase()),
  );

  return (
    <section
      id="task-board"
      className={`board-section ${showArchived ? "archive-board" : ""}`}
      aria-label="Task board"
    >
      <div className="board-toolbar">
        <h2>
          <LayoutGrid size={17} /> {showArchived ? "Archived tasks" : viewName}{" "}
          <span>{visible.length}</span>
        </h2>
        <div className={`filters ${showSearch ? "search-expanded" : ""}`}>
          <button
            type="button"
            className="icon-button help-toolbar-button"
            aria-label="How it works"
            title="How it works"
            onClick={onShowHelp}
          >
            <CircleHelp size={16} />
          </button>
          <button
            type="button"
            className="icon-button archive-view-button"
            aria-label={showArchived ? "Back to board" : "Show archived tasks"}
            title={showArchived ? "Back to board" : "Show archived tasks"}
            aria-pressed={showArchived}
            onClick={onToggleArchived}
          >
            {showArchived ? <LayoutGrid size={16} /> : <Archive size={16} />}
          </button>
          <div className={`search ${showSearch ? "search-open" : ""}`}>
            <button
              ref={searchToggle}
              type="button"
              className="search-toggle"
              aria-label={showSearch ? "Close search" : "Search tasks"}
              title={showSearch ? "Close search" : "Search tasks"}
              aria-expanded={showSearch}
              aria-controls="task-search"
              onClick={() => {
                if (showSearch) closeSearch();
                else onShowSearch(true);
              }}
            >
              {showSearch ? <X size={16} /> : <Search size={16} />}
            </button>
            {showSearch && (
              <input
                id="task-search"
                placeholder="Search tasks…"
                aria-label="Search tasks"
                value={search}
                onChange={(e) => onSearch(e.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") closeSearch();
                }}
                autoFocus
              />
            )}
          </div>
          {!showArchived && canAddTasks && (
            <button className="primary-button" onClick={onAddTask}>
              <Plus size={16} /> Add task
            </button>
          )}
        </div>
      </div>
      {tasks.isPending && (
        <div className="notice" role="status">
          <LoaderCircle size={18} className="spin" /> Gathering your tasks…
        </div>
      )}
      {tasks.isError && !tasks.data && (
        <div className="notice error" role="alert">
          Could not load tasks.{" "}
          <button className="text-button" onClick={() => void tasks.refetch()}>
            Try again
          </button>
        </div>
      )}
      {tasks.isError && tasks.data && (
        <div className="notice error" role="alert">
          Could not refresh tasks. Showing the last loaded board.{" "}
          <button className="text-button" onClick={() => void tasks.refetch()}>
            Try again
          </button>
        </div>
      )}
      {tasks.data && (
        <Kanban tasks={visible}>
          {columns
            .filter((column) => !showArchived || column.status === "completed")
            .map((column) => {
              const isDueCompleted =
                !showArchived &&
                selection === "due" &&
                column.status === "completed";
              const items = visible.filter((t) => t.status === column.status);
              const Icon = column.icon;
              const label = showArchived
                ? "Archived tasks"
                : isDueCompleted
                  ? "Move to Completed"
                  : column.label;

              return (
                <KanbanColumn
                  status={column.status}
                  key={column.status}
                  name={label}
                >
                  <div className="column-heading">
                    <span>
                      <Icon size={15} />
                      {label}
                      {!isDueCompleted && <b>{items.length}</b>}
                    </span>
                    <span className="column-dots" aria-hidden="true">
                      ···
                    </span>
                  </div>
                  {column.status === "pending" && canAddTasks && (
                    <button
                      className="add-task"
                      onClick={() => {
                        onAddTask();
                        document
                          .querySelector("main")
                          ?.scrollTo({ top: 0, behavior: "smooth" });
                      }}
                    >
                      <Plus size={15} /> Add a task
                    </button>
                  )}
                  {isDueCompleted ? (
                    <div
                      className="move-completed-target"
                      aria-label="Drop area to move task to completed"
                    >
                      <div className="move-completed-zone">
                        <CircleCheck size={28} />
                        <strong>Move to Completed</strong>
                        <p>Drag tasks here to mark them as completed</p>
                      </div>
                    </div>
                  ) : (
                    <div className="column-cards">
                      <CardList
                        key={`${selection}|${showArchived}|${search}`}
                        items={items}
                        isDueView={selection === "due"}
                        onOpen={onOpenTask}
                      />
                      {!items.length && (
                        <div className="empty-column">
                          <Icon size={24} />
                          <strong>
                            {search
                              ? "No matching tasks"
                              : showArchived
                                ? "No archived tasks"
                                : "A little breathing room"}
                          </strong>
                          <p>
                            {search
                              ? "Try another search."
                              : showArchived
                                ? "Completed tasks you archive appear here."
                                : column.hint}
                          </p>
                        </div>
                      )}
                    </div>
                  )}
                </KanbanColumn>
              );
            })}
        </Kanban>
      )}
    </section>
  );
}
