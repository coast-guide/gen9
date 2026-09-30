import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { keycloakify } from "keycloakify/vite-plugin";
import { defineConfig } from "vite";

// https://docs.keycloakify.dev
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    keycloakify({
      themeName: "gen9",
      // Read from the Keycloak container's environment at runtime (kcContext.properties)
      environmentVariables: [{ name: "GEN9_UI_URL", default: "http://localhost:14000" }],
      accountThemeImplementation: "none",
      // Keycloak 26+ only (the stack pins 26.7.4): one jar, no legacy 22-25 build
      keycloakVersionTargets: { "22-to-25": false, "all-other-versions": "gen9-keycloak-theme.jar" },
    }),
  ],
});
