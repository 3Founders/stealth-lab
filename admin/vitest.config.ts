import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: { environment: "jsdom", include: ["**/*.test.ts"], exclude: ["node_modules/**", ".next/**"] },
  resolve: { alias: { "@": path.resolve(__dirname, ".") } },
});
