import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { endSession } from "../session";

/** Keeps cached tasks, projects, comments, and members fresh from the
 * server's event stream, reconnecting with exponential backoff. */
export function useLiveUpdates() {
  const client = useQueryClient();
  useEffect(() => {
    let cancelled = false;
    let stream: EventSource | null = null;
    let retryTimer: number | undefined;
    let attempt = 0;
    const refresh = () => {
      if (!client.isMutating({ mutationKey: ["status"] })) {
        void client.invalidateQueries({ queryKey: ["tasks"], exact: false });
      }
      void client.invalidateQueries({ queryKey: ["projects"] });
      void client.invalidateQueries({ queryKey: ["comments"] });
      void client.invalidateQueries({ queryKey: ["members"] });
    };
    const reconnect = () => {
      stream?.close();
      stream = null;
      if (cancelled || retryTimer !== undefined) return;
      // 1s, 2s, 4s … capped at 30s; reset after the next successful `ready`.
      const delay = Math.min(30000, 1000 * 2 ** attempt);
      attempt += 1;
      retryTimer = window.setTimeout(() => {
        retryTimer = undefined;
        connect();
      }, delay);
    };
    const connect = () => {
      if (cancelled) return;
      const source = new EventSource("/api/events");
      stream = source;
      const current = (handler: () => void) => () => {
        if (stream === source) handler();
      };
      source.addEventListener(
        "ready",
        current(() => {
          attempt = 0;
          refresh();
        }),
      );
      source.addEventListener("task_changed", current(refresh));
      // The server asks for a fresh stream after a Redis interruption.
      source.addEventListener("reconnect", current(reconnect));
      source.addEventListener(
        "signed_out",
        current(() => {
          cancelled = true;
          source.close();
          endSession(client);
        }),
      );
      source.onerror = current(() => {
        // Network drops keep CONNECTING and retry on their own; an HTTP
        // error (502/503 during restarts, 401) closes the stream for good.
        if (source.readyState !== EventSource.CLOSED) return;
        // A 401 here is handled by the global session-expiry handler.
        void client.invalidateQueries({ queryKey: ["me"] });
        reconnect();
      });
    };
    connect();
    return () => {
      cancelled = true;
      window.clearTimeout(retryTimer);
      stream?.close();
    };
  }, [client]);
}
