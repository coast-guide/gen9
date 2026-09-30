// What an app a person allowed may do, in the words its consent screen used. Keycloak's account
// API gives each granted scope's consent text: Keycloak's own scopes a message key
// ("${emailScopeConsentText}"), which Gen9's theme words (gen9-keycloak/theme/src/login/i18n.ts),
// and Gen9's MCP and A2A scopes their words as configure.sh sets them. app-scopes.test.ts fails
// when the theme says something else.

export const THEME_WORDS: Record<string, string> = {
  offlineAccessScopeConsentText: "use your account while you’re not signed in",
  profileScopeConsentText: "see your name",
  emailScopeConsentText: "see your email address",
  rolesScopeConsentText: "see your role in Gen9 (member or admin)",
};

const keyOf = (consentText: string) => consentText.match(/^\$\{(\w+)\}$/)?.[1];

/** The consent texts in words, Gen9's own first (what the app is for), then Keycloak's in the theme's order. */
export function whatItMayDo(consentTexts: string[]): string[] {
  const order = Object.keys(THEME_WORDS);
  const rank = (text: string) => {
    const key = keyOf(text);
    return key === undefined ? -1 : order.includes(key) ? order.indexOf(key) : order.length;
  };
  return [...consentTexts].sort((a, b) => rank(a) - rank(b)).map((text) => {
    const key = keyOf(text);
    return key === undefined ? text : (THEME_WORDS[key] ?? key);
  });
}
