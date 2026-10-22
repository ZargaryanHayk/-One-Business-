import { useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, LogIn } from "lucide-react";
import { api, type User } from "../../api";
import { Brand } from "../../components/Brand";

/** Shows the confirmation for a `#token=` sign-in link before its exchange:
 * anyone can send a link that would otherwise silently replace this
 * browser's session (login CSRF). */
export function SignInLinkGate({
  token,
  children,
}: {
  token: string | null;
  children: ReactNode;
}) {
  const [pending, setPending] = useState(token);
  if (pending)
    return <LinkSignIn token={pending} onDone={() => setPending(null)} />;
  return children;
}

function LinkSignIn({ token, onDone }: { token: string; onDone: () => void }) {
  const client = useQueryClient();
  const current = useQuery({
    queryKey: ["me"],
    queryFn: () => api<User>("/me"),
  });
  const exchange = useMutation({
    // A rejected link must not end the session that is already signed in.
    meta: { ignoreUnauthorized: true },
    mutationFn: () =>
      api("/auth/exchange", {
        method: "POST",
        body: JSON.stringify({ token }),
      }),
    onSuccess: () => {
      // Forget everything cached for the previous account.
      client.clear();
      window.history.replaceState(null, "", "/dashboard");
      onDone();
    },
  });
  return (
    <div className="signin-shell">
      <Brand />
      <div className="signin link-signin">
        <span className="eyebrow">SIGN-IN LINK</span>
        <h1>Sign in with this link?</h1>
        <p className="link-signin-text">
          This link signs this browser in to the Taskline account that created
          it. Continuing replaces any account currently signed in here. Only
          continue if you requested the link yourself, for example with
          /dashboard in Telegram.
        </p>
        {current.data && (
          <div className="notice" role="status">
            You are signed in as {current.data.email || current.data.name}.
          </div>
        )}
        {exchange.error && (
          <div className="notice error" role="alert">
            {exchange.error.message}
          </div>
        )}
        <div className="link-signin-actions">
          <button
            type="button"
            className="text-button"
            disabled={exchange.isPending}
            onClick={onDone}
          >
            Cancel
          </button>
          <button
            type="button"
            className="primary-button"
            disabled={exchange.isPending}
            onClick={() => exchange.mutate()}
          >
            {exchange.isPending ? (
              <LoaderCircle size={16} className="spin" />
            ) : (
              <LogIn size={16} />
            )}{" "}
            Continue
          </button>
        </div>
      </div>
    </div>
  );
}
