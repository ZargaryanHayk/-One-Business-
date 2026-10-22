import { useRef } from "react";
import { Archive } from "lucide-react";
import { keepDialogFocus } from "../dialog";
import { useModalDialog } from "../hooks/useModalDialog";

export function ArchiveConfirmationDialog({
  kind,
  name,
  pending,
  error,
  onConfirm,
  onClose,
}: {
  kind: "task" | "project";
  name: string;
  pending: boolean;
  error?: string;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useModalDialog(dialog);

  return (
    <dialog
      ref={dialog}
      className="archive-confirmation-modal"
      aria-labelledby="archive-confirmation-title"
      aria-describedby="archive-confirmation-description"
      onKeyDown={keepDialogFocus}
      onCancel={(event) => {
        event.preventDefault();
        if (!pending) onClose();
      }}
    >
      <span className="modal-icon">
        <Archive size={27} />
      </span>
      <h2 id="archive-confirmation-title">Archive {kind}?</h2>
      <p id="archive-confirmation-description">
        Are you sure you want to archive <strong>{name}</strong>?{" "}
        {kind === "project"
          ? "Its tasks and comments will stay saved."
          : "Its details and comments will stay saved."}{" "}
        You can restore it later.
      </p>
      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}
      <div className="composer-actions">
        <button
          type="button"
          className="text-button"
          disabled={pending}
          onClick={onClose}
          autoFocus
        >
          No
        </button>
        <button
          type="button"
          className="primary-button"
          disabled={pending}
          onClick={onConfirm}
        >
          {pending ? "Archiving…" : "Yes"}
        </button>
      </div>
    </dialog>
  );
}
