import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const ORIGINAL_ENV = process.env.NEXT_PUBLIC_KEL_API_URL;

describe("kel-api module shape — no frontend ranking fallback", () => {
  it("does not export rankScore or verificationBucket", async () => {
    const mod = await import("@/lib/kel-api");
    expect("rankScore" in mod).toBe(false);
    expect("verificationBucket" in mod).toBe(false);
  });

  it("exposes getRankedProcedures as the only ranking source", async () => {
    const mod = await import("@/lib/kel-api");
    expect(typeof mod.getRankedProcedures).toBe("function");
  });
});

describe("kel-api economy fetchers — real endpoints, real params", () => {
  beforeEach(() => { process.env.NEXT_PUBLIC_KEL_API_URL = "http://backend.test"; });
  afterEach(() => { process.env.NEXT_PUBLIC_KEL_API_URL = ORIGINAL_ENV; vi.restoreAllMocks(); vi.resetModules(); });

  async function capturedPath(call: () => Promise<unknown>): Promise<string> {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await call();
    const [url] = fetchMock.mock.calls[0];
    return String(url).replace("http://backend.test", "");
  }

  it("getRankedProcedures hits the canonical backend ranking endpoint", async () => {
    vi.resetModules();
    const { getRankedProcedures } = await import("@/lib/kel-api");
    const path = await capturedPath(() => getRankedProcedures("goal-1"));
    expect(path).toBe("/v1/economy/goals/goal-1/procedures/ranked");
  });

  it("getRankedProcedures forwards an explicit context_key", async () => {
    vi.resetModules();
    const { getRankedProcedures } = await import("@/lib/kel-api");
    const path = await capturedPath(() => getRankedProcedures("goal-1", "kubernetes"));
    expect(path).toBe("/v1/economy/goals/goal-1/procedures/ranked?context_key=kubernetes");
  });

  it("getGoalContributors hits the canonical contributor endpoint (not a client tally)", async () => {
    vi.resetModules();
    const { getGoalContributors } = await import("@/lib/kel-api");
    const path = await capturedPath(() => getGoalContributors("goal-1"));
    expect(path).toBe("/v1/economy/goals/goal-1/contributors");
  });

  it("getCreditsBalance and getCreditsHistory are scoped to one contributor_id", async () => {
    vi.resetModules();
    const { getCreditsBalance, getCreditsHistory } = await import("@/lib/kel-api");
    const balancePath = await capturedPath(() => getCreditsBalance("sub-alice"));
    expect(balancePath).toBe("/v1/economy/contributors/sub-alice/credits");
    vi.resetModules();
    const { getCreditsHistory: getCreditsHistory2 } = await import("@/lib/kel-api");
    const historyPath = await capturedPath(() => getCreditsHistory2("sub-alice"));
    expect(historyPath).toBe("/v1/economy/contributors/sub-alice/credits/history?limit=50");
  });

  it("getStanding is scoped to one contributor_id", async () => {
    vi.resetModules();
    const { getStanding } = await import("@/lib/kel-api");
    const path = await capturedPath(() => getStanding("sub-alice"));
    expect(path).toBe("/v1/economy/contributors/sub-alice/standing");
  });

  it("createProcedureSubmission POSTs to the real submission endpoint with only the given fields", async () => {
    vi.resetModules();
    const fetchMock = vi.fn().mockResolvedValue(new Response('{"id":"s1","status":"candidate"}', { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const { createProcedureSubmission } = await import("@/lib/kel-api");
    const { setAccessToken, clearAccessToken } = await import("@/lib/session");
    setAccessToken("t");
    await createProcedureSubmission({ goal_id: "g1", submission_type: "new", name: "n", steps: ["s"] });
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("http://backend.test/v1/economy/procedure-submissions");
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body).not.toHaveProperty("submitted_by");
    expect(body).not.toHaveProperty("owner_id");
    expect(body).not.toHaveProperty("created_by");
    expect(body.goal_id).toBe("g1");
    clearAccessToken();
  });

  it("createBenchmarkSubmission never lets the caller attach a target procedure", async () => {
    vi.resetModules();
    const fetchMock = vi.fn().mockResolvedValue(new Response('{"id":"b1","status":"candidate"}', { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const { createBenchmarkSubmission } = await import("@/lib/kel-api");
    const { setAccessToken, clearAccessToken } = await import("@/lib/session");
    setAccessToken("t");
    await createBenchmarkSubmission({ goal_id: "g1", name: "bench" });
    const [, init] = fetchMock.mock.calls[0];
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body).not.toHaveProperty("procedure_id");
    expect(body).not.toHaveProperty("procedure_row_id");
    expect(body).not.toHaveProperty("submitted_by");
    clearAccessToken();
  });
});
