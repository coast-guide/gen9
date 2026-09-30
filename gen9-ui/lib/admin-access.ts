/**
 * Whether an admin call was refused because the caller isn't an admin. gen9-agent asks Keycloak
 * on every admin call, so access removed after the session's token was issued shows up here as a
 * 403 while the session still says admin (its roles come from that token).
 */
export function refusedAsNotAdmin(error: unknown): boolean {
  return typeof error === "object" && error !== null && (error as { status?: unknown }).status === 403;
}

/** An admin call's answer, or null when the API says the caller is no longer an admin. */
export async function orNotAdmin<T>(call: Promise<T>): Promise<T | null> {
  try {
    return await call;
  } catch (error) {
    if (refusedAsNotAdmin(error)) return null;
    throw error;
  }
}
