import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, LayoutGrid } from "lucide-react";
import {
  api,
  fetchAllTasks,
  taskAssigneeIds,
  type Project,
  type Task,
  type User,
} from "../../api";
import { ArchiveConfirmationDialog } from "../../components/ArchiveConfirmationDialog";
import { isDueSoon } from "../../dates";
import { useLiveUpdates } from "../../hooks/useLiveUpdates";
import { taskRefetchInterval } from "../../taskStatus";
import { LogoutDialog } from "../auth/LogoutDialog";
import { TaskBoard } from "../board/TaskBoard";
import { TaskComposer } from "../board/TaskComposer";
import { TaskOverview } from "../board/TaskOverview";
import { ArchivedProjectsModal } from "../projects/ArchivedProjectsModal";
import { ProjectCreator } from "../projects/ProjectCreator";
import { ProjectInfoModal } from "../projects/ProjectInfoModal";
import { TaskDetails } from "../tasks/TaskDetails";
import { HelpDialog } from "./HelpDialog";
import { Sidebar } from "./Sidebar";

export function Workspace({
  user,
  botUrl,
}: {
  user: User;
  botUrl?: string | null;
}) {
  const client = useQueryClient();
  const [search, setSearch] = useState("");
  const [showSearch, setShowSearch] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const [showLogout, setShowLogout] = useState(
    () => window.location.pathname === "/logout",
  );
  const closeLogout = () => {
    setShowLogout(false);
    if (window.location.pathname === "/logout") {
      window.history.replaceState(null, "", "/dashboard");
    }
  };
  const [showComposer, setShowComposer] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const [selection, setSelection] = useState<string>(
    () =>
      new URLSearchParams(window.location.search).get("project") ||
      (new URLSearchParams(window.location.search).get("view") === "due"
        ? "due"
        : "mine"),
  );
  const [showProjectCreator, setShowProjectCreator] = useState(false);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [infoProject, setInfoProject] = useState<Project | null>(null);
  const [projectToArchive, setProjectToArchive] = useState<Project | null>(
    null,
  );
  const [showProjectArchive, setShowProjectArchive] = useState(false);
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: () => api<Project[]>("/projects"),
    refetchInterval: 30000,
  });
  const archivedProjects = useQuery({
    queryKey: ["projects", "archived"],
    queryFn: () => api<Project[]>("/projects?archived=true"),
    refetchInterval: 30000,
  });

  const isManager = user.role === "project_manager" || user.role === "admin";
  const selectedProject = [
    ...(projects.data ?? []),
    ...(archivedProjects.data ?? []),
  ].find((project) => project.id === selection);
  const chooseView = (value: string) => {
    setSelection(value);
    setSearch("");
    setShowSearch(false);
    setShowComposer(false);
    setShowArchived(false);
    window.history.replaceState(
      null,
      "",
      value === "mine"
        ? "/dashboard"
        : value === "due"
          ? "/dashboard?view=due"
          : `/dashboard?project=${encodeURIComponent(value)}`,
    );
  };
  const projectArchive = useMutation({
    mutationFn: (project: Project) =>
      api<Project>(`/projects/${project.id}`, {
        method: "PATCH",
        body: JSON.stringify({ archived: !project.archived_at }),
      }),
    onSuccess: (project) => {
      void client.invalidateQueries({ queryKey: ["projects"] });
      void client.invalidateQueries({ queryKey: ["tasks"] });
      setInfoProject(null);
      setProjectToArchive(null);
      setDetailId(null);
      if (project.archived_at && selection === project.id) chooseView("mine");
      if (!project.archived_at) {
        setShowProjectArchive(false);
        chooseView(project.id);
      }
    },
  });

  // A project id from the URL must name a project this account can see;
  // anything else falls back to My Tasks instead of an error board.
  const isProjectView = selection !== "mine" && selection !== "due";
  const projectListsSettled =
    !projects.isPending && !archivedProjects.isPending;
  const [selectionChecked, setSelectionChecked] = useState(!isProjectView);
  useEffect(() => {
    if (selectionChecked || !projectListsSettled) return;
    setSelectionChecked(true);
    if (projects.isSuccess && archivedProjects.isSuccess && !selectedProject)
      chooseView("mine");
  }, [
    selectionChecked,
    projectListsSettled,
    projects.isSuccess,
    archivedProjects.isSuccess,
    selectedProject,
  ]);

  // Fetch all accessible tasks for sidebar counts and detail panel lookups.
  const allTasks = useQuery({
    queryKey: ["tasks", "all"],
    queryFn: ({ signal }) => fetchAllTasks("", signal),
    refetchInterval: taskRefetchInterval(client),
  });

  // Fetch scoped tasks for the current board view. Developers' Due soon view
  // lists their own tasks; managers see their accessible workspace.
  const scopedQuery = new URLSearchParams();
  if (selection === "mine") scopedQuery.set("mine", "true");
  else if (selection === "due") {
    if (!showArchived) {
      scopedQuery.set("due_soon", "true");
      if (!isManager) scopedQuery.set("mine", "true");
    }
  } else scopedQuery.set("project_id", selection);
  scopedQuery.set("archived", String(showArchived));
  const scopedParams = scopedQuery.toString();
  const tasks = useQuery({
    queryKey: ["tasks", selection, showArchived],
    queryFn: ({ signal }) => fetchAllTasks(scopedParams, signal),
    refetchInterval: taskRefetchInterval(client),
    enabled: !isProjectView || !!selectedProject || selectionChecked,
  });

  useLiveUpdates();

  const all = useMemo(() => allTasks.data ?? [], [allTasks.data]);
  const isMine = (task: Task) =>
    taskAssigneeIds(task).length
      ? taskAssigneeIds(task).includes(user.id)
      : !task.project_id && (!task.user_id || task.user_id === user.id);
  // Same rule as the Due soon board: developers count their own tasks.
  const dueTasks = all.filter(
    (task) => isDueSoon(task) && (isManager || isMine(task)),
  );
  const scoped = tasks.data ?? [];
  const detailTask = [...scoped, ...all].find((task) => task.id === detailId);
  const viewName =
    selectedProject?.name || (selection === "due" ? "Due soon" : "My Tasks");
  const canAddTasks =
    !selectedProject?.archived_at &&
    (selection === "mine" ||
      selection === "due" ||
      !!selectedProject?.can_manage);

  return (
    <div className="app-shell">
      <Sidebar
        user={user}
        botUrl={botUrl}
        isManager={isManager}
        selection={selection}
        myTaskCount={all.filter(isMine).length}
        dueCount={dueTasks.length}
        projects={projects}
        archivePending={projectArchive.isPending}
        onChooseView={chooseView}
        onShowArchivedProjects={() => {
          projectArchive.reset();
          setShowProjectArchive(true);
        }}
        onNewProject={() => setShowProjectCreator(true)}
        onProjectInfo={setInfoProject}
        onArchiveProject={(project) => {
          projectArchive.reset();
          setProjectToArchive(project);
        }}
        onShowHelp={() => setShowHelp(true)}
        onLogout={() => setShowLogout(true)}
      />
      <main>
        <header className="topbar">
          <span>
            <LayoutGrid size={15} /> Workspace{" "}
            <span className="breadcrumb">/</span>
            <strong>{viewName}</strong>
          </span>
        </header>
        <div className="main-content">
          {showProjectCreator && (
            <ProjectCreator
              onClose={() => setShowProjectCreator(false)}
              onCreated={(project) => {
                setShowProjectCreator(false);
                chooseView(project.id);
              }}
            />
          )}
          <label className="mobile-project-picker">
            View
            <select
              aria-label="Choose board"
              value={selection}
              onChange={(event) => chooseView(event.target.value)}
            >
              <option value="mine">My Tasks</option>
              <option value="due">Due soon</option>
              {selectedProject?.archived_at && (
                <option value={selectedProject.id}>
                  {selectedProject.name} (archived)
                </option>
              )}
              {projects.data?.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.name}
                </option>
              ))}
            </select>
          </label>
          {projectArchive.error && !showProjectArchive && !projectToArchive && (
            <p role="alert" className="card-error">
              {projectArchive.error.message}
            </p>
          )}
          {selectedProject?.archived_at && (
            <div className="project-archive-notice" role="status">
              <span>
                <Archive size={17} /> This project is archived. Its tasks and
                comments are saved.
              </span>
              {selectedProject.can_manage && (
                <button
                  className="text-button"
                  disabled={projectArchive.isPending}
                  onClick={() => projectArchive.mutate(selectedProject)}
                >
                  Restore project
                </button>
              )}
            </div>
          )}
          {showComposer && !selectedProject?.archived_at && (
            <TaskComposer
              key={selection}
              projects={projects.data ?? []}
              initialProject={
                selectedProject?.can_manage ? selectedProject.id : null
              }
              onClose={() => setShowComposer(false)}
            />
          )}
          <TaskOverview tasks={scoped} />
          <TaskBoard
            tasks={tasks}
            selection={selection}
            viewName={viewName}
            canAddTasks={canAddTasks}
            showArchived={showArchived}
            search={search}
            showSearch={showSearch}
            onSearch={setSearch}
            onShowSearch={setShowSearch}
            onToggleArchived={() => {
              setShowArchived(!showArchived);
              setShowComposer(false);
              setSearch("");
            }}
            onAddTask={() => setShowComposer(true)}
            onShowHelp={() => setShowHelp(true)}
            onOpenTask={setDetailId}
          />
        </div>
      </main>
      {showLogout && <LogoutDialog user={user} onClose={closeLogout} />}
      {projectToArchive && (
        <ArchiveConfirmationDialog
          kind="project"
          name={projectToArchive.name}
          pending={projectArchive.isPending}
          error={projectArchive.error?.message}
          onConfirm={() => projectArchive.mutate(projectToArchive)}
          onClose={() => {
            setProjectToArchive(null);
            projectArchive.reset();
          }}
        />
      )}
      {showProjectArchive && (
        <ArchivedProjectsModal
          projects={archivedProjects.data ?? []}
          loading={archivedProjects.isPending}
          error={archivedProjects.error?.message}
          pending={projectArchive.isPending}
          mutationError={projectArchive.error?.message}
          onClose={() => setShowProjectArchive(false)}
          onRestore={(project) => projectArchive.mutate(project)}
          onView={(project) => {
            setShowProjectArchive(false);
            chooseView(project.id);
          }}
        />
      )}
      {detailTask && (
        <TaskDetails
          key={detailId}
          task={detailTask}
          onClose={() => setDetailId(null)}
        />
      )}
      {infoProject && (
        <ProjectInfoModal
          key={infoProject.id}
          project={infoProject}
          onClose={() => setInfoProject(null)}
        />
      )}
      {showHelp && (
        <HelpDialog botUrl={botUrl} onClose={() => setShowHelp(false)} />
      )}
    </div>
  );
}
