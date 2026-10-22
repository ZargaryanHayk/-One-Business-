import type { QueryClient } from "@tanstack/react-query";

let ending = false;

/** Drop all cached account data and return to the login page (once). */
export function endSession(client: QueryClient) {
  if (ending) return;
  ending = true;
  client.clear();
  window.location.assign("/login");
}
