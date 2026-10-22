import { useRef } from "react";
import { useMutation } from "@tanstack/react-query";
import { LogOut, X } from "lucide-react";
import { api, ApiError, type User } from "../../api";
import { keepDialogFocus } from "../../dialog";
import { useModalDialog } from "../../hooks/useModalDialog";

export function LogoutDialog({
  user,
  onClose,
}: {
  user: User;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const logout = useMutation({
    mutationFn: async (allDevices: boolean) => {
      try {
        await api(allDevices ? "/auth/logout-all" : "/auth/logout", {
          method: "POST",
        });
      } catch (error) {
        // An already expired or revoked session is as logged out as it gets.
        if (!(error instanceof ApiError && error.status === 401)) throw error;
      }
    },
    onSuccess: () => window.location.assign("/login?logged_out=1"),
  });
  useModalDialog(dialog);
  return (
    <dialog
      ref={dialog}
      className="logout-modal"
      onKeyDown={keepDialogFocus}
      aria-labelledby="logout-title"
      aria-describedby="logout-description"
      aria-modal="true"
      onCancel={(event) => {
        event.preventDefault();
        if (!logout.isPending) onClose();
      }}
    >
      <button
        className="icon-button close-modal"
        aria-label="Cancel logout"
        disabled={logout.isPending}
        onClick={onClose}
      >
        <X size={19} />
      </button>
      <span className="modal-icon">
        <LogOut size={27} />
      </span>
      <h2 id="logout-title">Log out of your account?</h2>
      <p id="logout-description">
        Choose where to log out of {user.email || user.name}.
      </p>
      {logout.error && (
        <div className="notice error" role="alert">
          {logout.error.message}
        </div>
      )}
      <div className="logout-actions">
        <button
          className="primary-button"
          disabled={logout.isPending}
          onClick={() => logout.mutate(false)}
        >
          {logout.isPending && !logout.variables
            ? "Logging out…"
            : "Log out on this device"}
        </button>
        <button
          className="logout-all-button"
          disabled={logout.isPending}
          onClick={() => logout.mutate(true)}
        >
          {logout.isPending && logout.variables
            ? "Logging out…"
            : "Log out on all devices"}
        </button>
      </div>
      <p className="logout-hint">
        Both options clear recent Telegram chat messages. All devices also ends
        every website and Telegram session.
      </p>
    </dialog>
  );
}
