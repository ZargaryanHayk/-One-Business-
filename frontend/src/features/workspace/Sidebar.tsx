import type { UseQueryResult } from "@tanstack/react-query";
import {
  Archive,
  ArrowUpRight,
  CalendarClock,
  FolderKanban,
  LayoutGrid,
  LogOut,
  Plus,
  Send,
  Sparkles,
} from "lucide-react";
import type { Project, User } from "../../api";
import { Brand } from "../../components/Brand";
import { TelegramButton } from "../../components/TelegramButton";
import { ProjectMenu } from "../projects/ProjectMenu";

export function Sidebar({
  user,
  botUrl,
  isManager,
  selection,
  myTaskCount,
  dueCount,
  projects,
  archivePending,
  onChooseView,
  onShowArchivedProjects,
  onNewProject,
  onProjectInfo,
  onArchiveProject,
  onShowHelp,
  onLogout,
}: {
  user: User;
  botUrl?: string | null;
  isManager: boolean;
  selection: string;
  myTaskCount: number;
  dueCount: number;
  projects: UseQueryResult<Project[]>;
  archivePending: boolean;
  onChooseView: (value: string) => void;
  onShowArchivedProjects: () => void;
  onNewProject: () => void;
  onProjectInfo: (project: Project) => void;
  onArchiveProject: (project: Project) => void;
  onShowHelp: () => void;
  onLogout: () => void;
}) {
  return (
    <aside className="sidebar">
      <Brand label="Taskline home" />
      <div className="workspace-label">
        {user.role?.replace("_", " ").toUpperCase() || "PERSONAL WORKSPACE"}
      </div>
      <button
        className={`nav-item ${selection === "mine" ? "active" : ""}`}
        aria-label="My tasks"
        onClick={() => {
          onChooseView("mine");
          document
            .getElementById("task-board")
            ?.scrollIntoView({ behavior: "smooth" });
        }}
      >
        <LayoutGrid size={17} />
        <span>My tasks</span>
        <span className="nav-count">{myTaskCount}</span>
      </button>
      <button
        className={`nav-item ${selection === "due" ? "active" : ""}`}
        aria-label="Due soon"
        title="Due soon"
        onClick={() => onChooseView("due")}
      >
        <CalendarClock size={17} />
        <span>Due soon</span>
        <span className="nav-count">{dueCount}</span>
      </button>
      <div className="project-navigation">
        <div className="project-nav-heading">
          <span>PROJECTS</span>
          <button
            className="icon-button"
            aria-label="Show archived projects"
            title="Archived projects"
            onClick={onShowArchivedProjects}
          >
            <Archive size={16} />
          </button>
          {isManager && (
            <button
              className="icon-button"
              aria-label="New project"
              title="New project"
              onClick={onNewProject}
            >
              <Plus size={16} />
            </button>
          )}
        </div>
        {projects.isError && (
          <button
            className="text-button"
            onClick={() => void projects.refetch()}
          >
            Retry projects
          </button>
        )}
        {projects.data?.map((project) => (
          <div
            key={project.id}
            className={`project-nav-row ${selection === project.id ? "active" : ""}`}
          >
            <button
              title={project.name}
              aria-label={`Project ${project.name}`}
              className="nav-item project-link"
              onClick={() => onChooseView(project.id)}
            >
              <FolderKanban size={16} />
              <span className="project-link-name">{project.name}</span>
            </button>
            <ProjectMenu
              project={project}
              onInfo={onProjectInfo}
              onArchive={onArchiveProject}
              pending={archivePending}
            />
          </div>
        ))}
        {projects.data?.length === 0 && (
          <small className="project-empty">
            {isManager
              ? "Create your first project"
              : "Your assigned projects appear here"}
          </small>
        )}
      </div>
      {botUrl && (
        <TelegramButton className="nav-item telegram-nav">
          <Send size={17} />
          <span>Open Telegram</span>
          <ArrowUpRight className="nav-arrow" size={13} />
        </TelegramButton>
      )}
      <div className="sidebar-note">
        <span className="sidebar-orbit">
          <Sparkles size={20} />
        </span>
        <h3>
          Less remembering.
          <br />
          More doing.
        </h3>
        <p>
          Your next idea is just a<br />
          Telegram message away.
        </p>
        <button onClick={onShowHelp}>
          How it works <ArrowUpRight size={14} />
        </button>
      </div>
      <div className="profile">
        <span className="avatar">{user.name.charAt(0).toUpperCase()}</span>
        <div>
          <strong>{user.name}</strong>
          <small>{user.email || "Personal account"}</small>
        </div>
      </div>
      <button className="logout-button" aria-label="Log out" onClick={onLogout}>
        <LogOut size={16} />
        <span>Log out</span>
      </button>
    </aside>
  );
}
