// @vitest-environment node
// The organization admin client and its pure helpers (lib/org-admin.ts). Nothing here touches a network.
import { describe, expect, it } from "vitest";
import { ADMIN_ROLES, canAdmin, isPublicOnly, lastDays, listFromText, monthRange, nameFor, orgAdminEnabled, orgApi, totalsByUser, usd, usedShare, type Member, type UserUsageRow } from "@/lib/org-admin";

const rec = () => {
  const seen: Array<{ url: string; init: RequestInit }> = [];
  const make = (status: number, body: unknown) => (async (url: string, init: RequestInit) => { seen.push({ url, init }); return status === 204 ? new Response(null, { status }) : new Response(JSON.stringify(body), { status }); }) as unknown as typeof fetch;
  return { seen, make };
};
const api = (f: typeof fetch) => orgApi({ token: async () => "TOK", origin: "https://mcp.example.org/", fetchImpl: f });

describe("flag", () => {
  it("is off unless exactly 'on'", () => {
    expect(orgAdminEnabled(undefined)).toBe(false);
    expect(orgAdminEnabled("")).toBe(false);
    expect(orgAdminEnabled("true")).toBe(false);
    expect(orgAdminEnabled(" ON ")).toBe(true);
  });
});

describe("client", () => {
  it("makes no request when signed out", async () => {
    let called = false;
    const a = orgApi({ token: async () => null, fetchImpl: (async () => { called = true; return new Response("{}"); }) as unknown as typeof fetch });
    await expect(a.mine()).rejects.toMatchObject({ status: 401 });
    expect(called).toBe(false);
  });
  it("sends the session token as a Bearer, to the host, with no cookies", async () => {
    const r = rec();
    await api(r.make(200, [])).mine();
    expect(r.seen[0].url).toBe("https://mcp.example.org/v1/orgs/mine");
    expect((r.seen[0].init.headers as Record<string, string>).authorization).toBe("Bearer TOK");
    expect(r.seen[0].init.credentials).toBe("omit");
  });
  it("uses the routes of backend/app/api/org_admin.py", async () => {
    const r = rec();
    const a = api(r.make(200, { members: [], rows: [], calls: [], counts_by_reason: [], events: [] }));
    await a.policy("o1"); await a.putPolicy("o1", { monthly_budget_usd: "5" }, 3); await a.recommendedPolicy("o1", "100", "5");
    await a.killSwitch("o1", true, "why"); await a.members("o1"); await a.invites("o1"); await a.createInvite("o1", { label: "x", days: 3, max_uses: 2 });
    await a.join("CODE"); await a.budget("o1"); await a.usageByUser("o1", "2026-10-01", "2026-10-29");
    await a.calls("o1", "2026-10-01", "2026-10-29", { model: "m", status: "failed", user: "u", limit: 50 }); await a.denials("o1", "2026-10-01", "2026-10-29");
    await a.providerKeys("o1"); await a.putProviderKey("o1", "main", { provider: "p", base_url: "https://x", units: [], allowed_data_classes: ["PUBLIC_SOURCE"] });
    await a.onboarding("o1"); await a.clientConfig("o1");
    expect(r.seen.map((s) => `${s.init.method} ${s.url.replace("https://mcp.example.org", "")}`)).toEqual([
      "GET /v1/orgs/o1/policy", "PUT /v1/orgs/o1/policy", "POST /v1/orgs/o1/policy/recommended", "POST /v1/orgs/o1/kill-switch",
      "GET /v1/orgs/o1/members", "GET /v1/orgs/o1/invites", "POST /v1/orgs/o1/invites", "POST /v1/join", "GET /v1/orgs/o1/budget",
      "GET /v1/orgs/o1/usage/users?since=2026-10-01&until=2026-10-29",
      "GET /v1/orgs/o1/calls?since=2026-10-01&until=2026-10-29&model=m&status=failed&user=u&limit=50",
      "GET /v1/orgs/o1/denials?since=2026-10-01&until=2026-10-29", "GET /v1/orgs/o1/provider-keys", "PUT /v1/orgs/o1/provider-keys/main",
      "GET /v1/orgs/o1/onboarding", "GET /v1/orgs/o1/client-config",
    ]);
    expect(JSON.parse(String(r.seen[1].init.body))).toEqual({ policy: { monthly_budget_usd: "5" }, expected_version: 3 });
    expect(JSON.parse(String(r.seen[3].init.body))).toEqual({ on: true, reason: "why" });
    expect(JSON.parse(String(r.seen[7].init.body))).toEqual({ code: "CODE" });
  });
  it("shows the server's own sentence, and reads 204 as success", async () => {
    await expect(api(rec().make(403, { detail: "reading the policy needs the role owner or admin" })).policy("o1")).rejects.toMatchObject({ message: "reading the policy needs the role owner or admin", status: 403 });
    await expect(api(rec().make(409, { detail: "this organization has no policy" })).budget("o1")).rejects.toMatchObject({ status: 409 });
    await expect(api(rec().make(204, null)).removeProviderKey("o1", "main")).resolves.toBeUndefined();
    await expect(api(rec().make(404, {})).policy("o1")).rejects.toMatchObject({ message: "Not found." });
  });
  it("never sends a stored key back: a replace without a key carries no api_key", async () => {
    const r = rec();
    await api(r.make(200, {})).putProviderKey("o1", "main", { provider: "p", base_url: "https://x", units: [], allowed_data_classes: ["PUBLIC_SOURCE"] });
    expect("api_key" in JSON.parse(String(r.seen[0].init.body))).toBe(false);
  });
});

describe("helpers", () => {
  it("roles", () => {
    expect(ADMIN_ROLES).toEqual(["owner", "admin"]);
    expect([canAdmin("owner"), canAdmin("admin"), canAdmin("member"), canAdmin("viewer"), canAdmin(undefined)]).toEqual([true, true, false, false, false]);
  });
  it("money and shares", () => {
    expect(usd("12.5")).toBe("$12.50");
    expect(usd("0.0012")).toBe("$0.0012");
    expect(usd(null)).toBe("–");
    expect(usedShare("30", "10", "100")).toBe(40);
    expect(usedShare("500", "0", "100")).toBe(100);
    expect(usedShare("1", "0", "0")).toBeNull();
  });
  it("ranges are UTC, with an exclusive end", () => {
    const now = new Date("2026-10-28T23:30:00Z");
    expect(monthRange(now)).toEqual({ since: "2026-10-01", until: "2026-10-29" });
    expect(lastDays(7, now)).toEqual({ since: "2026-10-22", until: "2026-10-29" });
  });
  it("totals per person, biggest spender first, and names from members", () => {
    const row = (u: string, cost: string, out: number): UserUsageRow => ({ organization_id: "o", actor_subject: u, day: "d", calls: 2, failed: 1, tokens_input_fresh: 10, tokens_cache_read: 0, tokens_cache_write: 0, tokens_output: out, cost_usd: cost, upper_bound_calls: 0 });
    const t = totalsByUser([row("a", "1.5", 5), row("b", "4", 1), row("a", "1", 5)]);
    expect(t.map((x) => [x.user, x.cost, x.calls, x.tokens])).toEqual([["b", 4, 2, 11], ["a", 2.5, 4, 30]]);
    const members: Member[] = [{ user_id: "u1", subject: "sub-1", display_name: "Ada", email: "a@x.co", is_active: true, roles: ["member"], member_since: "2026-01-01" }];
    expect(nameFor("sub-1", members)).toBe("Ada");
    expect(nameFor("0123456789abcdef", members)).toBe("01234567…");
  });
  it("public data only is exactly the public classes", () => {
    expect(isPublicOnly(["PUBLIC_SOURCE", "PUBLIC_DERIVED", "GLOBAL_PROCEDURE"])).toBe(true);
    expect(isPublicOnly(["PUBLIC_SOURCE"])).toBe(true);
    expect(isPublicOnly(["PUBLIC_SOURCE", "USER_PRIVATE"])).toBe(false);
    expect(isPublicOnly([])).toBe(false);
    expect(listFromText("a, b  c,a")).toEqual(["a", "b", "c"]);
  });
});
