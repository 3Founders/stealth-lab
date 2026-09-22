import path from "node:path";
import { defineConfig } from "vitest/config";

// Minimal, logic-only test setup — no component rendering / testing-library,
// no Storybook. Covers lib/*.ts: session handling, the authenticated
// fetch wrapper, and the API surface's shape (no fabricated ranking, no
// leaked identity fields). jsdom only because lib/session.ts touches
// `window.localStorage`/`atob`.
export default defineConfig({
  test: {
    environment: "jsdom",
    include: ["**/*.test.ts", "**/*.test.tsx"],
    exclude: ["node_modules/**", ".next/**"],
  },
  resolve: {
    alias: { "@": path.resolve(__dirname, ".") },
  },
});
