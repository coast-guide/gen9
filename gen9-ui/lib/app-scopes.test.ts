import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { THEME_WORDS, whatItMayDo } from "@/lib/app-scopes";

const read = (path: string) => readFileSync(fileURLToPath(new URL(path, import.meta.url)), "utf8");

describe("app scopes", () => {
  // Settings says what an app may do as its consent screen said it (docs/plans/gen9-learn.md, M9, F12)
  it("words Keycloak's scopes as Gen9's consent screen does", () => {
    const theme = read("../../gen9-keycloak/theme/src/login/i18n.ts");
    for (const [key, words] of Object.entries(THEME_WORDS)) expect(theme).toContain(`${key}: "${words}"`);
  });

  it("keeps Gen9's own scopes' words, and puts them first", () => {
    const mcp = "use Gen9 from this app: ask it, and read and search your chats";
    // As configure.sh sets it on the gen9-mcp scope, which the account API gives as it is
    expect(read("../../gen9-keycloak/config/configure.sh")).toContain(`bound_scope gen9-mcp "$GEN9_MCP_URL" "${mcp}"`);
    expect(whatItMayDo(["${rolesScopeConsentText}", "${emailScopeConsentText}", mcp, "${profileScopeConsentText}"])).toEqual([
      mcp,
      "see your name",
      "see your email address",
      "see your role in Gen9 (member or admin)",
    ]);
    // A key the theme doesn't word is named, not hidden
    expect(whatItMayDo(["${someScopeConsentText}"])).toEqual(["someScopeConsentText"]);
  });
});
