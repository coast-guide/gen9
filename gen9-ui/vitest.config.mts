import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

// Unit tests for server modules (Next.js Vitest guide); async Server Components belong in E2E tests
export default defineConfig({
  resolve: {
    tsconfigPaths: true, // "@/..." imports, resolved natively by Vite 8
    alias: { "server-only": fileURLToPath(new URL("./test/server-only.ts", import.meta.url)) },
  },
  test: {
    environment: "node",
    include: ["**/*.test.ts"],
    exclude: ["node_modules/**", ".next/**"],
  },
});
