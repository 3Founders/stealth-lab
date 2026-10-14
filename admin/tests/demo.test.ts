import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiGet, apiPost } from "@/lib/api";
import { demoEnabled, demoResponse } from "@/lib/demo";
import { toMicros } from "@/lib/money";

const Q = "since=2020-01-01T00:00:00Z&until=2999-01-01T00:00:00Z";
const get = (path: string) => { const r = demoResponse(path); if (r.kind !== "ok") throw new Error(`${path}: ${r.kind}`); return r.data as any; };

beforeEach(() => vi.stubEnv("NEXT_PUBLIC_DEMO_DATA", "1"));
afterEach(() => vi.unstubAllEnvs());

describe("switch", () => {
  it("is off unless explicitly enabled, and never on in a production build", () => {
    vi.stubEnv("NEXT_PUBLIC_DEMO_DATA", "");
    expect(demoEnabled()).toBe(false);
    vi.stubEnv("NEXT_PUBLIC_DEMO_DATA", "1");
    expect(demoEnabled()).toBe(true);
    vi.stubEnv("NODE_ENV", "production");
    expect(demoEnabled()).toBe(false);
  });
  it("the API client answers from sample data only while enabled, and refuses writes", async () => {
    expect((await apiGet<unknown[]>("/v1/orgs/mine")).kind).toBe("ok");
    const w = await apiPost("/v1/orgs/x/kill-switch", { on: true, reason: "x" });
    expect(w.kind).toBe("error");
    vi.stubEnv("NEXT_PUBLIC_DEMO_DATA", "");
    expect((await apiGet("/v1/orgs/mine")).kind).toBe("unconfigured"); // real path: no backend URL set in tests
  });
});

describe("internal consistency (the same invariants the real ledger keeps)", () => {
  it("cost components + unattributed equal each row's total, to the micro-dollar", () => {
    const rows = get(`/v1/orgs/demo-organisation/usage?${Q}`).rows;
    expect(rows.length).toBeGreaterThan(50);
    for (const r of rows) {
      const parts = [r.cost_input_usd, r.cost_output_usd, r.cost_cache_read_usd, r.cost_cache_write_usd, r.cost_unattributed_usd].reduce((s: bigint, v: string) => s + toMicros(v), 0n);
      expect(parts).toBe(toMicros(r.cost_usd));
      expect(r.succeeded + r.failed).toBe(r.calls);
    }
  });
  it("organisation totals equal the sum of the per-person totals", () => {
    const org = get(`/v1/orgs/demo-organisation/usage?${Q}`).rows;
    const users = get(`/v1/orgs/demo-organisation/usage/users?${Q}`).rows;
    const sum = (rs: any[], f: (r: any) => bigint) => rs.reduce((s, r) => s + f(r), 0n);
    expect(sum(users, (r) => toMicros(r.cost_usd))).toBe(sum(org, (r) => toMicros(r.cost_usd)));
    expect(sum(users, (r) => BigInt(r.calls))).toBe(sum(org, (r) => BigInt(r.calls)));
    expect(sum(users, (r) => BigInt(r.failed))).toBe(sum(org, (r) => BigInt(r.failed)));
  });
  it("the range filter returns only days inside the window", () => {
    const today = new Date().toISOString().slice(0, 10);
    const rows = get(`/v1/orgs/demo-organisation/usage?since=${today}T00:00:00Z&until=2999-01-01T00:00:00Z`).rows;
    expect(rows.every((r: any) => r.day === today)).toBe(true);
  });
  it("has one spike day, some unpriced calls, and an unreported-cache provider", () => {
    const rows = get(`/v1/orgs/demo-organisation/usage?${Q}`).rows;
    expect(rows.some((r: any) => r.upper_bound_calls > 0)).toBe(true);
    const perf = get(`/v1/orgs/demo-organisation/performance?${Q}`).rows;
    expect(perf.some((r: any) => r.cache_hit_rate_requests === null)).toBe(true);  // not reported, not 0
    expect(perf.some((r: any) => r.tier === null)).toBe(true);                     // untiered
  });
  it("is deterministic for a given day", () => {
    const a = get(`/v1/orgs/demo-organisation/usage?${Q}`);
    const b = get(`/v1/orgs/demo-organisation/usage?${Q}`);
    expect(JSON.stringify(a)).toBe(JSON.stringify(b));
  });
});

describe("other endpoints", () => {
  it("budget: used + held + remaining = budget, and today's per-person rows exist", () => {
    const b = get("/v1/orgs/demo-organisation/budget");
    const m = b.monthly;
    expect(toMicros(m.used_usd) + toMicros(m.held_usd) + toMicros(m.remaining_usd)).toBe(toMicros(m.budget_usd));
    for (const u of b.users_today) expect(toMicros(u.used_usd) + toMicros(u.held_usd) + toMicros(u.remaining_usd)).toBe(toMicros(b.per_user_daily_budget_usd));
  });
  it("denials: the counts add up to the events", () => {
    const d = get(`/v1/orgs/demo-organisation/denials?${Q}`);
    expect(d.counts_by_reason.reduce((s: number, c: any) => s + c.count, 0)).toBe(d.events.length);
  });
  it("audit: newest first when asked, with the 'order' key the client looks for", () => {
    const desc = get(`/v1/orgs/demo-organisation/audit?${Q}&order=desc&limit=3`);
    expect(desc.order).toBe("desc");
    expect(desc.events.length).toBe(3);
    expect(desc.events[0].id).toBeGreaterThan(desc.events[1].id);
    const asc = get(`/v1/orgs/demo-organisation/audit?${Q}`);
    expect(asc.order).toBeUndefined();
    expect(asc.chain_intact).toBe(true);
  });
  it("members use the same subject as the usage rows, and are plainly fake", () => {
    const subjects = new Set(get("/v1/orgs/demo-organisation/members").members.map((m: any) => m.subject));
    for (const r of get(`/v1/orgs/demo-organisation/usage/users?${Q}`).rows) expect(subjects.has(r.actor_subject)).toBe(true);
    expect(get("/v1/orgs/demo-organisation/members").members[0].email).toMatch(/example\.invalid$/);
  });
  it("calls honour filters", () => {
    const all = get(`/v1/orgs/demo-organisation/calls?${Q}`).calls;
    const light = get(`/v1/orgs/demo-organisation/calls?${Q}&model=demo-light`).calls;
    expect(light.length).toBeGreaterThan(0);
    expect(light.length).toBeLessThan(all.length);
    expect(light.every((c: any) => c.model === "demo-light")).toBe(true);
  });
  it("anything outside the sample is an honest 404, not invented", () => {
    expect(demoResponse("/v1/admin/ingestion/auto-status")).toMatchObject({ kind: "error", status: 404 });
    expect(demoResponse("/v1/orgs/demo-organisation/compliance-nonsense")).toMatchObject({ kind: "error", status: 404 });
  });
});
