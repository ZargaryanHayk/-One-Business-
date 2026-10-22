import type { ReactNode } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "../api";

export function TelegramButton({
  className,
  children,
}: {
  className: string;
  children: ReactNode;
}) {
  const login = useMutation({
    mutationFn: async (popup: Window | null) => {
      try {
        const { url } = await api<{ url: string }>("/auth/telegram-link", {
          method: "POST",
        });
        if (popup && !popup.closed) popup.location.replace(url);
        else window.location.assign(url);
      } catch (error) {
        popup?.close();
        throw error;
      }
    },
  });

  return (
    <>
      <button
        type="button"
        className={className}
        aria-label="Open Telegram"
        disabled={login.isPending}
        aria-busy={login.isPending}
        onClick={() => {
          // Open during the click so browsers allow the new tab.
          const popup = window.open("about:blank", "_blank");
          if (popup) popup.opener = null;
          login.mutate(popup);
        }}
      >
        {children}
      </button>
      {login.error && (
        <p className="notice error" role="alert">
          {login.error.message}
        </p>
      )}
    </>
  );
}
