import { createGetKcContextMock } from "keycloakify/login/KcContext";

import { kcEnvDefaults, themeNames } from "../kc.gen";
import type { KcContextExtension, KcContextExtensionPerPage } from "./KcContext";

const RECOVERY_CODES = Array.from({ length: 12 }, (_, i) => `WM${i}W`.padEnd(12, "M").slice(0, 12));

// Dev-only preview data (npm run dev), shaped like what Keycloak renders into the page
export const { getKcContextMock } = createGetKcContextMock({
  kcContextExtension: { themeName: themeNames[0], properties: { ...kcEnvDefaults } } satisfies KcContextExtension,
  kcContextExtensionPerPage: {} satisfies KcContextExtensionPerPage,
  overrides: { realm: { displayName: "Gen9", registrationEmailAsUsername: true } },
  overridesPerPage: {
    "login.ftl": { realm: { loginWithEmailAllowed: true, rememberMe: true, resetPasswordAllowed: true, registrationAllowed: true } },
    // Keycloak generates 12 codes of 12 characters; the default mock has 3 short ones
    "login-recovery-authn-code-config.ftl": {
      recoveryAuthnCodesConfigBean: {
        generatedRecoveryAuthnCodesList: RECOVERY_CODES,
        generatedRecoveryAuthnCodesAsString: RECOVERY_CODES.join(","),
        generatedAt: Date.UTC(2026, 8, 24),
      },
    },
    "login-recovery-authn-code-input.ftl": { recoveryAuthnCodesInputBean: { codeNumber: 3 } },
  },
});
