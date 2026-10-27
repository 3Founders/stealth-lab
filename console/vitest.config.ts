import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  esbuild: { jsx: "automatic" },
  test: { environment: "node", include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"], exclude: ["node_modules/**", "out/**"], testTimeout: 20000 },
  resolve: { alias: { "@": path.resolve(__dirname, ".") } },
});
