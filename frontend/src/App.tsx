import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { LoaderCircle } from "lucide-react";
import { api, ApiError, type PublicConfig, type User } from "./api";
import { Brand } from "./components/Brand";
import { SignIn } from "./features/auth/SignIn";
import { Workspace } from "./features/workspace/Workspace";
import { VoiceReview } from "./features/voice-review/VoiceReview";

export function App() {
  return window.location.pathname === "/voice-review" ? (
    <VoiceReview />
  ) : (
    <WorkspaceApp />
  );
}

function WorkspaceApp() {
  const configuration = useQuery({
    queryKey: ["config"],
    queryFn: () => api<PublicConfig>("/config"),
    refetchInterval: 30000,
    refetchOnWindowFocus: false,
  });
  const user = useQuery({
    queryKey: ["me"],
    queryFn: () => api<User>("/me"),
    refetchOnWindowFocus: false,
  });
  useEffect(() => {
    if (
      user.data &&
      !["/dashboard", "/logout"].includes(window.location.pathname)
    ) {
      window.history.replaceState(null, "", "/dashboard");
    } else if (
      !user.data &&
      user.error instanceof ApiError &&
      user.error.status === 401 &&
      window.location.pathname !== "/login"
    ) {
      window.history.replaceState(null, "", "/login");
    }
  }, [user.data, user.error]);
  if (user.isLoading)
    return (
      <div className="full-loader">
        <LoaderCircle className="spin" size={26} />
        <span>Opening your workspace…</span>
      </div>
    );
  if (!user.data)
    return (
      <div className="signin-shell">
        <Brand />
        <SignIn
          botUrl={configuration.data?.telegram_bot_url}
          error={
            user.error instanceof ApiError && user.error.status === 401
              ? ""
              : "Unable to reach your workspace. Please try again."
          }
        />
      </div>
    );
  return (
    <Workspace user={user.data} botUrl={configuration.data?.telegram_bot_url} />
  );
}
