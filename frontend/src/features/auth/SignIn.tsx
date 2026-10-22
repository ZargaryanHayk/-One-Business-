import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ArrowUpRight,
  Check,
  Eye,
  EyeOff,
  LoaderCircle,
  LogOut,
  Send,
} from "lucide-react";
import { api, type User } from "../../api";

export function SignIn({
  error,
  botUrl,
}: {
  error?: string;
  botUrl?: string | null;
}) {
  const client = useQueryClient();
  const [loggedOut] = useState(() =>
    new URLSearchParams(window.location.search).has("logged_out"),
  );
  const [savedEmail] = useState(() => {
    try {
      return sessionStorage.getItem("taskline_email") || "";
    } catch {
      return "";
    }
  });
  useEffect(() => {
    const url = new URL(window.location.href);
    if (url.searchParams.has("logged_out")) {
      url.searchParams.delete("logged_out");
      window.history.replaceState(window.history.state, "", url);
    }
  }, []);
  const form = useRef<HTMLFormElement>(null);
  const passwordInput = useRef<HTMLInputElement>(null);
  const [showPassword, setShowPassword] = useState(false);
  const clearPassword = () => {
    if (passwordInput.current) passwordInput.current.value = "";
    setShowPassword(false);
  };
  const login = useMutation({
    mutationFn: () => {
      if (!form.current) throw new Error("Please try logging in again.");
      const credentials = new FormData(form.current);
      return api<User>("/auth/login", {
        method: "POST",
        body: JSON.stringify({
          email: String(credentials.get("email") || "").trim(),
          password: String(credentials.get("password") || ""),
        }),
      });
    },
    onSuccess: (user) => {
      clearPassword();
      try {
        sessionStorage.removeItem("taskline_email");
      } catch {
        // ignore storage errors
      }
      client.removeQueries({ queryKey: ["tasks"] });
      client.setQueryData(["me"], user);
      window.history.replaceState(null, "", "/dashboard");
    },
    onError: clearPassword,
  });
  const failure = login.error?.message || error;
  return (
    <div className="signin">
      <div className="signin-illustration">
        <Send size={38} />
        <span>
          <Check size={18} />
        </span>
      </div>
      <span className="eyebrow">FROM A THOUGHT TO A TASK</span>
      <h1>
        Your tasks.
        <br />
        One web dashboard.
      </h1>
      {loggedOut && (
        <div className="notice" role="status">
          You have been logged out.
        </div>
      )}
      {failure && (
        <div className="notice error" role="alert">
          {failure}
        </div>
      )}
      <form
        ref={form}
        className="login-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (!login.isPending) login.mutate();
        }}
      >
        <label htmlFor="login-email">Email</label>
        <input
          id="login-email"
          name="email"
          type="email"
          autoComplete="username"
          required
          maxLength={254}
          placeholder="you@example.com"
          defaultValue={savedEmail}
          onInput={(e) => {
            try {
              sessionStorage.setItem("taskline_email", e.currentTarget.value);
            } catch {
              // ignore storage errors
            }
          }}
        />
        <label htmlFor="login-password">Password</label>
        <div className="password-field">
          <input
            id="login-password"
            ref={passwordInput}
            name="password"
            type={showPassword ? "text" : "password"}
            autoComplete="current-password"
            required
            maxLength={256}
          />
          <button
            className="password-toggle"
            type="button"
            aria-label={showPassword ? "Hide password" : "Show password"}
            aria-controls="login-password"
            onClick={() => setShowPassword((visible) => !visible)}
          >
            {showPassword ? <EyeOff size={19} /> : <Eye size={19} />}
          </button>
        </div>
        <button
          className="primary-button"
          type="submit"
          disabled={login.isPending}
        >
          {login.isPending ? (
            <LoaderCircle size={16} className="spin" />
          ) : (
            <LogOut size={16} />
          )}{" "}
          Log in
        </button>
      </form>
      {botUrl && (
        <a
          className="primary-button telegram-login"
          href={`${botUrl}?start=login`}
          target="_blank"
          rel="noreferrer"
        >
          <Send size={17} /> Log in with Telegram <ArrowUpRight size={16} />
        </a>
      )}
    </div>
  );
}
