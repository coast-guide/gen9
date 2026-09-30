import "server-only";

import { env } from "@/lib/env";

/**
 * CSRF defense for state-changing Route Handlers (Server Actions check this themselves):
 * the browser's Origin header must be this app. SameSite=Lax cookies are the first line.
 */
export function isSameOrigin(request: Request): boolean {
  const origin = request.headers.get("origin");
  return origin !== null && origin === new URL(env().APP_URL).origin;
}
