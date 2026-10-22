import { useRef, useState } from "react";
import { Archive, Info, MoreVertical } from "lucide-react";
import type { Project } from "../../api";

export function ProjectMenu({
  project,
  onInfo,
  onArchive,
  pending,
}: {
  project: Project;
  onInfo: (project: Project) => void;
  onArchive: (project: Project) => void;
  pending: boolean;
}) {
  const menu = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const id = `project-menu-${project.id}`;

  return (
    <div className="project-menu-container">
      <button
        ref={trigger}
        type="button"
        className={`icon-button project-more-button ${open ? "menu-open" : ""}`}
        aria-label={`Options for ${project.name}`}
        title="Project options"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={id}
        popoverTarget={id}
        onClick={() => {
          const rect = trigger.current?.getBoundingClientRect();
          if (rect && menu.current) {
            menu.current.style.left = `${Math.max(8, Math.min(rect.right - 140, window.innerWidth - 148))}px`;
            menu.current.style.top = `${Math.max(8, Math.min(rect.bottom + 4, window.innerHeight - (project.can_manage ? 100 : 56)))}px`;
          }
        }}
      >
        <MoreVertical size={16} />
      </button>
      <div
        ref={menu}
        id={id}
        className="project-dropdown-menu"
        role="menu"
        aria-label={`Options for ${project.name}`}
        popover="auto"
        onToggle={(event) => {
          const visible = event.newState === "open";
          setOpen(visible);
          if (visible) menu.current?.querySelector("button")?.focus();
        }}
      >
        <button
          type="button"
          className="dropdown-item"
          role="menuitem"
          onClick={() => {
            menu.current?.hidePopover();
            trigger.current?.focus();
            onInfo(project);
          }}
        >
          <Info size={14} /> Info
        </button>
        {project.can_manage && (
          <button
            type="button"
            className="dropdown-item"
            role="menuitem"
            disabled={pending}
            onClick={() => {
              menu.current?.hidePopover();
              trigger.current?.focus();
              onArchive(project);
            }}
          >
            <Archive size={14} /> Archive project
          </button>
        )}
      </div>
    </div>
  );
}
