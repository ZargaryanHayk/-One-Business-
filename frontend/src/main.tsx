import { createRoot } from "react-dom/client";
import {
  MutationCache,
  QueryCache,
  QueryClient,
  QueryClientProvider,
  focusManager,
} from "@tanstack/react-query";
import { App } from "./App";
import { ApiError } from "./api";
import { SignInLinkGate } from "./features/auth/LinkSignIn";
import { endSession } from "./session";
import "./styles.css";

// Prevent window focus or macOS space switching from triggering query refetches
focusManager.setEventListener(() => () => {});

// One place for session expiry: any 401 while signed in logs the browser out.
// TanStack keeps the previous `me` data on a failed refetch, so components
// cannot rely on `!user.data`. The login page's own /api/me probe and failed
// logins happen without `me` data and are left to the page.
const onUnauthorized = (error: unknown, meta?: Record<string, unknown>) => {
  if (!(error instanceof ApiError) || error.status !== 401) return;
  if (meta?.ignoreUnauthorized) return;
  if (!client.getQueryData(["me"])) return;
  endSession(client);
};

const client: QueryClient = new QueryClient({
  queryCache: new QueryCache({
    onError: (error, query) => onUnauthorized(error, query.meta),
  }),
  mutationCache: new MutationCache({
    onError: (error, _variables, _context, mutation) =>
      onUnauthorized(error, mutation.meta),
  }),
  defaultOptions: {
    queries: {
      retry: false,
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
    },
  },
});

const fragment = new URLSearchParams(window.location.hash.slice(1));
const linkToken = fragment.get("token") || null;
if (fragment.has("token")) {
  // Remove the secret before any network request, logging, or UI render.
  window.history.replaceState(
    null,
    "",
    window.location.pathname + window.location.search,
  );
}

createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={client}>
    <SignInLinkGate token={linkToken}>
      <App />
    </SignInLinkGate>
  </QueryClientProvider>,
);
