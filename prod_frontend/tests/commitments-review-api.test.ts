import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/session", () => ({ getAccessToken: vi.fn(async () => null) }));

const ORIGINAL_ENV = process.env.NEXT_PUBLIC_KEL_API_URL;

async function mockToken(token: string | null) {
  const { getAccessToken } = await import("@/lib/session");
  vi.mocked(getAccessToken).mockResolvedValue(token);
}

function stubFetch(body: unknown = {}) {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("Credit commitments and hierarchy review clients", () => {
  beforeEach(() => { process.env.NEXT_PUBLIC_KEL_API_URL = "http://backend.test"; vi.resetModules(); });
  afterEach(() => { process.env.NEXT_PUBLIC_KEL_API_URL = ORIGINAL_ENV; vi.restoreAllMocks(); vi.unstubAllGlobals(); });

  it("reads a Goal's demand totals without requiring sign-in", async () => {
    const fetchMock = stubFetch({ goal_id: "g1", direct: {}, aggregated: {}, mine: [], resolved: false });
    const { getGoalCommitments } = await import("@/lib/kel-api");
    const res = await getGoalCommitments("g1");
    expect(res.kind).toBe("ok");
    expect(String(fetchMock.mock.calls[0][0])).toBe("http://backend.test/v1/economy/goals/g1/commitments");
  });

  it("commits with an idempotency key and never sends an identity field", async () => {
    await mockToken("token-1");
    const fetchMock = stubFetch({ id: "c1", goal_id: "g1", credits: 5, created: true });
    const { commitToGoal } = await import("@/lib/kel-api");
    await commitToGoal("g1", 5, "key-1");
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("http://backend.test/v1/economy/goals/g1/commitments");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ credits: 5, idempotency_key: "key-1" });   // identity is server-derived
  });

  it("refuses to commit or withdraw when signed out", async () => {
    await mockToken(null);
    const fetchMock = stubFetch();
    const { commitToGoal, withdrawCommitment } = await import("@/lib/kel-api");
    expect((await commitToGoal("g1", 5, "k")).kind).toBe("unauthenticated");
    expect((await withdrawCommitment("g1", "c1")).kind).toBe("unauthenticated");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("withdraws with DELETE on the commitment", async () => {
    await mockToken("token-1");
    const fetchMock = stubFetch({ commitment_id: "c1", released: 5 });
    const { withdrawCommitment } = await import("@/lib/kel-api");
    await withdrawCommitment("g1", "c/1");
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("http://backend.test/v1/economy/goals/g1/commitments/c%2F1");
    expect(init.method).toBe("DELETE");
  });

  it("decides a proposed relation with both goals, a decision and a reason", async () => {
    await mockToken("token-1");
    const fetchMock = stubFetch({ relation: {} });
    const { decideProposedRelation } = await import("@/lib/kel-api");
    await decideProposedRelation("child", "parent", "accept", "specific case");
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("http://backend.test/v1/goal-review/relations/decide");
    expect(JSON.parse(init.body)).toEqual({
      specific_goal_id: "child", abstract_goal_id: "parent", decision: "accept", reason: "specific case",
    });
  });
});
