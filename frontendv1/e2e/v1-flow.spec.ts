import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * FINAL-V1 scripted browser E2E (freeze prompt §3).
 *
 * Drives the real V1 user flow through the actual Next frontend against the
 * real FastAPI backend + real Supabase Postgres. Nothing is mocked: the
 * spec fails if any product request is served by something other than the
 * configured backend origin, and it asserts on values that only the real
 * backend leaderboard recompute can produce (n = 30, 28 verified for the
 * seeded solution A).
 *
 * Fixture: e2e/seed_v1_flow.py (run first) -> e2e/.seed.json.
 */

const seed = JSON.parse(
  readFileSync(join(__dirname, ".seed.json"), "utf8"),
) as {
  problem_id: string;
  problem_title: string;
  benchmark_name: string;
  solution_a_id: string;
  evaluation_a_id: string;
  current_best: string[];
  viewer_id: string;
};

const API_ORIGIN = new URL(
  process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000",
).origin;

test.describe("Final-V1 product path", () => {
  // Every product API call the app makes, captured for the no-mock assertion.
  const apiCalls: string[] = [];

  test.beforeEach(async ({ page, context }) => {
    apiCalls.length = 0;
    page.on("request", (req) => {
      const u = req.url();
      if (u.includes("/v1/")) apiCalls.push(u);
    });
    // dev-fallback viewer identity, exactly as the /auth page would set it
    // (signInAsViewer stores the raw id under "stealth.viewer")
    await context.addInitScript(
      ([key, id]) => {
        try {
          sessionStorage.setItem(key, id);
        } catch {
          /* private mode */
        }
      },
      ["stealth.viewer", seed.viewer_id],
    );
  });

  test("app loads with the real backend", async ({ page }) => {
    await page.goto("/");
    await expect(
      page.getByRole("heading", { name: "Stealth Lab", level: 1 }),
    ).toBeVisible();
    // health of the API the app is pointed at
    const health = await page.request.get(`${API_ORIGIN}/health`);
    expect(health.ok()).toBeTruthy();
    expect((await health.json()).status).toBe("ok");
  });

  test("viewer identity is attached to backend requests", async ({ page }) => {
    let sawViewerHeader = false;
    page.on("request", (req) => {
      if (req.url().includes("/v1/problems")) {
        const h = req.headers();
        if (h["x-viewer-id"] === seed.viewer_id) sawViewerHeader = true;
      }
    });
    await page.goto("/problems");
    await expect(page.getByTestId("problem-list")).toBeVisible();
    expect(sawViewerHeader).toBeTruthy();
  });

  test("Problems list is backed by GET /v1/problems and shows the seeded problem", async ({
    page,
  }) => {
    await page.goto("/problems");
    const list = page.getByTestId("problem-list");
    await expect(list).toBeVisible();
    await expect(
      list.getByRole("link", { name: seed.problem_title }),
    ).toBeVisible();
    expect(apiCalls.some((u) => u.includes("/v1/problems"))).toBeTruthy();
    // no request left the configured backend origin
    for (const u of apiCalls) expect(new URL(u).origin).toBe(API_ORIGIN);
  });

  test("Problem detail: benchmark, solutions, leaderboard and current-best all render from backend data", async ({
    page,
  }) => {
    await page.goto(`/problems/${seed.problem_id}`);

    // title
    await expect(
      page.getByRole("heading", { name: seed.problem_title }),
    ).toBeVisible();

    // current best verified hero — derived by the backend Wilson lower bound,
    // not by the page
    const hero = page.getByTestId("current-best-hero");
    await expect(hero).toBeVisible();
    await expect(hero).toContainText("Current best verified");
    // the seeded solution A is 28/30 verified -> "93.3% verified success", n = 30
    await expect(hero).toContainText("93.3% verified success");
    await expect(hero).toContainText("n = 30");

    // leaderboard table
    const board = page.locator("#leaderboard");
    await expect(board).toBeVisible();
    await expect(board.getByRole("table")).toBeVisible();
    await expect(board).toContainText("Wilson lower bound");

    // solutions count line ("2 candidate solutions · 1 verified · 2 evaluated")
    await expect(page.getByText(/candidate solutions/)).toContainText(
      "2 candidate solutions",
    );

    // benchmark section
    const bench = page.locator("section", { hasText: "Benchmark" }).last();
    await expect(bench).toContainText(seed.benchmark_name);

    // evaluations list links to /evaluations/{id}
    await expect(
      page.getByRole("link", { name: /completed ·/ }).first(),
    ).toBeVisible();

    // every product call stayed on the real backend origin
    expect(apiCalls.length).toBeGreaterThan(0);
    for (const u of apiCalls) {
      expect(new URL(u).origin).toBe(API_ORIGIN);
      expect(u).not.toMatch(/mock|fixture|stub|fake/i);
    }
    // the calls that must have happened
    expect(
      apiCalls.some((u) => u.includes(`/v1/problems/${seed.problem_id}/leaderboard`)),
    ).toBeTruthy();
    expect(
      apiCalls.some((u) => u.includes(`/v1/problems/${seed.problem_id}/benchmarks`)),
    ).toBeTruthy();
  });

  test("Evaluation detail shows the backend-recomputed n and verified success", async ({
    page,
  }) => {
    await page.goto(`/evaluations/${seed.evaluation_a_id}`);
    await expect(
      page.getByRole("heading", { name: "How this result was measured" }),
    ).toBeVisible();
    await expect(page.getByText("n = 30")).toBeVisible();
    // 28/30 verified -> 93.3%, scoped to the "Verified success" metric
    const verifiedMetric = page
      .locator("div")
      .filter({ has: page.getByText("Verified success", { exact: true }) })
      .first();
    await expect(verifiedMetric).toContainText("93.3%");
    // status badge
    await expect(page.getByText("completed", { exact: false }).first()).toBeVisible();
    // context link back to the problem
    await expect(
      page.getByRole("link", { name: "this problem" }),
    ).toHaveAttribute("href", `/problems/${seed.problem_id}`);
  });

  test("primary flow completes: home -> problems -> problem -> evaluation", async ({
    page,
  }) => {
    await page.goto("/");
    await page.getByRole("link", { name: "Problems" }).click();
    await expect(page).toHaveURL(/\/problems$/);
    await page.getByRole("link", { name: seed.problem_title }).click();
    await expect(page).toHaveURL(new RegExp(`/problems/${seed.problem_id}$`));
    await expect(page.locator("#leaderboard").getByRole("table")).toBeVisible();
    await page.getByRole("link", { name: /completed ·/ }).first().click();
    await expect(page).toHaveURL(/\/evaluations\//);
    await expect(
      page.getByRole("heading", { name: "How this result was measured" }),
    ).toBeVisible();
  });

  test("WebMCP surface: feature-detects and exposes the semantic tool list", async ({
    page,
  }) => {
    await page.goto("/webmcp");
    await expect(
      page.getByRole("heading", { name: "WebMCP status" }),
    ).toBeVisible();
    // headless Chromium has no document.modelContext -> feature-detect says so
    await expect(page.getByText("document.modelContext")).toBeVisible();
    // the expected semantic tool list is rendered (>= 10 tools)
    const tools = page.locator("ul li code");
    expect(await tools.count()).toBeGreaterThanOrEqual(10);
    // a couple of the domain tools by name
    await expect(page.locator("code", { hasText: "search" }).first()).toBeVisible();
  });
});
