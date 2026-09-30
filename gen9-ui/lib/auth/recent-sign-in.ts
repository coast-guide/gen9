/**
 * Sensitive actions (deleting the account) need a real sign-in from the last 5 minutes, not just a
 * live session. gen9-agent enforces the same window from the token's auth_time (RFC 9470).
 */
export const RECENT_SIGN_IN_S = 300;

export function signedInRecently(authTime: number | null, nowMs = Date.now()): boolean {
  return authTime !== null && nowMs / 1000 - authTime <= RECENT_SIGN_IN_S;
}
