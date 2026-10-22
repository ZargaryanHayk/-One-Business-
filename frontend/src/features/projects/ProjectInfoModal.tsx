import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  FolderKanban,
  LoaderCircle,
  ShieldCheck,
  Trash2,
  Users,
  X,
} from "lucide-react";
import { api, type Project, type User } from "../../api";
import { keepDialogFocus } from "../../dialog";
import { useModalDialog } from "../../hooks/useModalDialog";

export function ProjectInfoModal({
  project,
  onClose,
}: {
  project: Project;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const panel = useRef<HTMLDialogElement>(null);
  const [email, setEmail] = useState("");
  const [confirmRemoveId, setConfirmRemoveId] = useState<string | null>(null);

  const people = useQuery({
    queryKey: ["members", project.id],
    queryFn: () => api<User[]>(`/projects/${project.id}/members`),
  });
  const accounts = useQuery({
    queryKey: ["accounts"],
    queryFn: () => api<User[]>("/users"),
    enabled: project.can_manage,
  });

  const add = useMutation({
    mutationFn: () =>
      api<User>(`/projects/${project.id}/members`, {
        method: "POST",
        body: JSON.stringify({ email: email.trim() }),
      }),
    onSuccess: () => {
      setEmail("");
      void client.invalidateQueries({ queryKey: ["members", project.id] });
      void client.invalidateQueries({ queryKey: ["projects"] });
      void client.invalidateQueries({ queryKey: ["tasks"], exact: false });
    },
  });

  const remove = useMutation({
    mutationFn: (memberId: string) =>
      api(`/projects/${project.id}/members/${memberId}`, {
        method: "DELETE",
      }),
    onSuccess: () => {
      setConfirmRemoveId(null);
      void client.invalidateQueries({ queryKey: ["members", project.id] });
      void client.invalidateQueries({ queryKey: ["projects"] });
      void client.invalidateQueries({ queryKey: ["tasks"], exact: false });
    },
  });

  useModalDialog(panel);

  return (
    <dialog
      ref={panel}
      className="project-info-modal"
      onKeyDown={keepDialogFocus}
      aria-labelledby="project-info-title"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target !== event.currentTarget) return;
        const rect = event.currentTarget.getBoundingClientRect();
        if (
          event.clientX < rect.left ||
          event.clientX > rect.right ||
          event.clientY < rect.top ||
          event.clientY > rect.bottom
        )
          onClose();
      }}
    >
      <button
        className="icon-button close-modal"
        aria-label="Close project info"
        onClick={onClose}
      >
        <X size={19} />
      </button>
      <span className="eyebrow">PROJECT INFORMATION</span>
      <div className="project-info-header">
        <span className="project-icon-badge">
          <FolderKanban size={22} />
        </span>
        <div>
          <h2 id="project-info-title">{project.name}</h2>
          <div className="project-info-badges">
            <span className="info-chip">
              <Users size={12} />
              {people.data?.length ?? 0}{" "}
              {people.data?.length === 1 ? "person" : "people"}
            </span>
            {project.can_manage ? (
              <span className="info-chip manager">
                <ShieldCheck size={12} /> Manager
              </span>
            ) : (
              <span className="info-chip member">Member</span>
            )}
          </div>
        </div>
      </div>

      <div className="project-members-section">
        <div className="section-header">
          <h3>People in this project</h3>
          {people.isPending && <LoaderCircle size={14} className="spin" />}
        </div>

        {people.isError && (
          <p role="alert" className="card-error">
            Could not load project people.
          </p>
        )}

        <div className="members-list">
          {people.data?.map((person) => {
            const isOwner = person.id === project.owner_id;
            const isRemoving =
              remove.isPending && remove.variables === person.id;
            return (
              <div key={person.id} className="member-card">
                <span className="member-avatar">
                  {person.name.charAt(0).toUpperCase()}
                </span>
                <div className="member-meta">
                  <div className="member-primary">
                    <strong>{person.name}</strong>
                    {isOwner && <span className="owner-tag">Owner</span>}
                  </div>
                  {person.email && (
                    <small className="member-email">{person.email}</small>
                  )}
                </div>
                {project.can_manage && !project.archived_at && !isOwner && (
                  <div className="member-actions">
                    {confirmRemoveId === person.id ? (
                      <div className="confirm-remove">
                        <span>Remove?</span>
                        <button
                          type="button"
                          className="delete-button small"
                          disabled={remove.isPending}
                          onClick={() => remove.mutate(person.id)}
                        >
                          {isRemoving ? "Removing…" : "Yes"}
                        </button>
                        <button
                          type="button"
                          className="text-button small"
                          disabled={remove.isPending}
                          onClick={() => setConfirmRemoveId(null)}
                        >
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <button
                        type="button"
                        className="icon-button remove-person-btn"
                        aria-label={`Remove ${person.name} from project`}
                        title={`Remove ${person.name}`}
                        disabled={remove.isPending}
                        onClick={() => setConfirmRemoveId(person.id)}
                      >
                        <Trash2 size={14} />
                      </button>
                    )}
                  </div>
                )}
              </div>
            );
          })}
          {people.data && people.data.length === 0 && (
            <p className="muted">No members in this project.</p>
          )}
        </div>

        {remove.error && (
          <p role="alert" className="card-error">
            {remove.error.message}
          </p>
        )}
      </div>

      {project.can_manage && !project.archived_at ? (
        <div className="add-person-section">
          <h3>Add a person</h3>
          <form
            className="add-person-form"
            onSubmit={(event) => {
              event.preventDefault();
              if (email.trim()) add.mutate();
            }}
          >
            <label className="visually-hidden" htmlFor="modal-member-email">
              Person email
            </label>
            <input
              id="modal-member-email"
              type="email"
              placeholder="Add by email address…"
              list="modal-project-accounts"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              disabled={add.isPending}
            />
            <datalist id="modal-project-accounts">
              {accounts.data
                ?.filter((acc) => !people.data?.some((p) => p.id === acc.id))
                .map((person) => (
                  <option key={person.id} value={person.email}>
                    {person.name}
                  </option>
                ))}
            </datalist>
            <button
              className="primary-button"
              type="submit"
              disabled={!email.trim() || add.isPending}
            >
              {add.isPending ? "Adding…" : "Add person"}
            </button>
          </form>
          {add.error && (
            <p role="alert" className="card-error">
              {add.error.message}
            </p>
          )}
        </div>
      ) : (
        <p className="project-modal-footer-note">
          {project.archived_at
            ? "Restore this project to manage people."
            : "Only project managers can add or remove people from this project."}
        </p>
      )}
    </dialog>
  );
}
