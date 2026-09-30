/**
 * Whether the person ticked "Remember me" at sign-in, from what Keycloak's token endpoint says
 * about the refresh token. Keycloak ends an ordinary session after 30 minutes idle and 10 hours at
 * most (the realm's ssoSessionIdleTimeout and ssoSessionMaxLifespan); a remembered one lasts 14
 * days idle (ssoSessionIdleTimeoutRememberMe). A refresh token that outlives the longest ordinary
 * session therefore belongs to a remembered one.
 */
export const ORDINARY_SESSION_S = 10 * 60 * 60;

export function rememberedSession(refreshExpiresInSeconds: number): boolean {
  return refreshExpiresInSeconds > ORDINARY_SESSION_S;
}
