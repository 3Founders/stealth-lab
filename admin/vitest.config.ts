import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  // Page tests are .tsx; tsconfig keeps "jsx": "preserve" for Next, so tell the test transformer to compile JSX itself.
  esbuild: { jsx: "automatic" },
  test: { environment: "jsdom", include: ["**/*.test.ts", "**/*.test.tsx"], exclude: ["node_modules/**", ".next/**"] },
  resolve: { alias: { "@": path.resolve(__dirname, ".") } },
});
