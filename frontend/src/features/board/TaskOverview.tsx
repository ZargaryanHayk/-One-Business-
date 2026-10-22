import { CircleCheck, Clock3, ListTodo } from "lucide-react";
import type { Task } from "../../api";

export function TaskOverview({ tasks }: { tasks: Task[] }) {
  const completed = tasks.filter((t) => t.status === "completed").length;
  const progress = tasks.length
    ? Math.round((completed / tasks.length) * 100)
    : 0;
  return (
    <section className="overview" aria-label="Task overview">
      <div className="stat">
        <span className="stat-icon lavender">
          <ListTodo size={19} />
        </span>
        <div>
          <span>Total tasks</span>
          <strong>
            {tasks.length}
            <small>tasks in this view</small>
          </strong>
        </div>
      </div>
      <div className="stat">
        <span className="stat-icon peach">
          <Clock3 size={19} />
        </span>
        <div>
          <span>In progress</span>
          <strong>
            {tasks.filter((t) => t.status === "in_progress").length}
            <small>moving forward</small>
          </strong>
        </div>
      </div>
      <div className="stat">
        <span className="stat-icon mint">
          <CircleCheck size={19} />
        </span>
        <div>
          <span>Completed</span>
          <strong>
            {completed}
            <small>off your mind</small>
          </strong>
        </div>
      </div>
      <div className="progress-stat">
        <div>
          <span>Look at you go</span>
          <strong>{progress}%</strong>
        </div>
        <div className="progress-track">
          <span style={{ width: `${progress}%` }} />
        </div>
        <small>
          {tasks.length
            ? `${completed} of ${tasks.length} tasks completed`
            : "Add your first task here or in Telegram"}
        </small>
      </div>
    </section>
  );
}
