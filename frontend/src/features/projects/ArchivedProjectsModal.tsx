import { useRef } from "react";
import { ArchiveRestore, X } from "lucide-react";
import type { Project } from "../../api";
import { keepDialogFocus } from "../../dialog";
import { useModalDialog } from "../../hooks/useModalDialog";

export function ArchivedProjectsModal({
  projects,
  loading,
  error,
  pending,
  mutationError,
  onRestore,
  onView,
  onClose,
}: {
  projects: Project[];
  loading: boolean;
  error?: string;
  pending: boolean;
  mutationError?: string;
  onRestore: (project: Project) => void;
  onView: (project: Project) => void;
  onClose: () => void;
}) {
  const panel = useRef<HTMLDialogElement>(null);
  useModalDialog(panel, { lockScroll: false });
  return (
    <dialog
      ref={panel}
      className="project-info-modal"
      aria-labelledby="archived-projects-title"
      onKeyDown={keepDialogFocus}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <button
        type="button"
        className="icon-button close-modal"
        aria-label="Close archived projects"
        onClick={onClose}
      >
        <X size={19} />
      </button>
      <span className="eyebrow">PROJECTS</span>
      <h2 id="archived-projects-title">Archived projects</h2>
      <p className="muted">
        Tasks and comments stay saved. Restore a project to continue working.
      </p>
      {loading && <p role="status">Loading archived projects…</p>}
      {error && (
        <p role="alert" className="card-error">
          {error}
        </p>
      )}
      {!loading && !error && !projects.length && <p>No archived projects.</p>}
      <div className="archived-project-list">
        {projects.map((project) => (
          <div className="archived-project-row" key={project.id}>
            <strong>{project.name}</strong>
            <div>
              <button
                type="button"
                className="text-button"
                onClick={() => onView(project)}
                aria-label={`View archived project ${project.name}`}
              >
                View
              </button>
              {project.can_manage && (
                <button
                  type="button"
                  className="primary-button"
                  disabled={pending}
                  onClick={() => onRestore(project)}
                  aria-label={`Restore project ${project.name}`}
                >
                  <ArchiveRestore size={15} /> Restore
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
      {mutationError && (
        <p role="alert" className="card-error">
          {mutationError}
        </p>
      )}
    </dialog>
  );
}
