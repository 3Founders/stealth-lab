import { defineConfig, devices } from "@playwright/test";

/**
 * Final-V1 scripted browser E2E (§3 of the freeze prompt).
 *
 * Runs the real V1 user flow through the actual Next frontend against the
 * real FastAPI backend + real Supabase Postgres -- no mock server, no
 * fixture doubles. The backend (uvicorn app.main:app on :8000, DATABASE_URL
 * pointed at Supabase) must already be running; this config only starts the
 * frontend. Seed data is created by e2e/seed_v1_flow.py, which writes
 * e2e/.seed.json for the spec.
 */
// localhost:3000 so the browser Origin matches the backend's default
// FRONTEND_ORIGIN (CORS allow-list) without any backend reconfiguration.
const PORT = Number(process.env.E2E_FRONTEND_PORT ?? 3000);
const HOST = process.env.E2E_FRONTEND_HOST ?? "localhost";
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  testDir: "./e2e",
  testMatch: /.*\.spec\.ts/,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  reporter: [["list"]],
  timeout: 60_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL: `http://${HOST}:${PORT}`,
    trace: "retain-on-failure",
    headless: true,
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
  webServer: {
    command: `npx next dev --port ${PORT} --hostname ${HOST}`,
    url: `http://${HOST}:${PORT}`,
    reuseExistingServer: false,
    timeout: 120_000,
    env: {
      NEXT_PUBLIC_API_URL: API_URL,
    },
  },
});
