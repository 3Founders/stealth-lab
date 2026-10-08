import { beforeEach, describe, expect, it, vi } from "vitest";

const apiGet = vi.fn();
vi.mock("@/lib/api", () => ({ apiGet: (...a: unknown[]) => apiGet(...a), apiPost: vi.fn(), apiPut: vi.fn(), apiDelete: vi.fn(), API_URL: "http://x" }));
vi.mock("@/lib/session", () => ({ getAccessToken: async () => "t" }));

import { getAuditRecent } from "@/lib/org-api";

const ev = (id: number) => ({ id, t_created: "2026-09-01T00:00:00Z", action: "org_policy.update", actor_subject: null, actor_user_id: null, object_type: null, object_id: null, tenant_id: null, details: null, prev_hash: null, row_hash: null });
const page = (extra: object) => ({ kind: "ok", data: { events: [], next_after_id: null, chain_intact: true, first_broken_id: null, ...extra } });

beforeEach(() => apiGet.mockReset());

describe("getAuditRecent", () => {
  it("uses order=desc when the backend supports it: one request, newest first", async () => {
    apiGet.mockResolvedValueOnce(page({ order: "desc", events: [ev(9), ev(8)], next_before_id: 7 }));
    const r = await getAuditRecent("o", "2026-09-01T00:00:00Z", "2026-09-30T00:00:00Z", 50);
    expect(apiGet).toHaveBeenCalledTimes(1);
    expect(String(apiGet.mock.calls[0][0])).toContain("order=desc");
    expect(r.kind === "ok" && r.data.newestFirst && r.data.events.map((e) => e.id)).toEqual([9, 8]);
  });

  it("falls back to walking the pages when the reply has no 'order' key (older backend)", async () => {
    apiGet.mockResolvedValueOnce(page({ events: [ev(1)], next_after_id: 1 })); // ignored the param: oldest-first, no order key
    apiGet.mockResolvedValueOnce(page({ events: [ev(1)], next_after_id: 2 }));
    apiGet.mockResolvedValueOnce(page({ events: [ev(2)], next_after_id: null }));
    const r = await getAuditRecent("o", "a", "b");
    expect(r.kind === "ok" && r.data.newestFirst).toBe(false);
    expect(r.kind === "ok" && r.data.events.map((e) => e.id)).toEqual([1, 2]);
    expect(r.kind === "ok" && r.data.truncated).toBe(false);
  });

  it("falls back on a 422 from an older backend", async () => {
    apiGet.mockResolvedValueOnce({ kind: "error", message: "422", status: 422 });
    apiGet.mockResolvedValueOnce(page({ events: [ev(5)], next_after_id: null }));
    const r = await getAuditRecent("o", "a", "b");
    expect(r.kind).toBe("ok");
    expect(apiGet).toHaveBeenCalledTimes(2);
  });

  it("does not retry (or invent data) when the caller isn't permitted", async () => {
    apiGet.mockResolvedValueOnce({ kind: "forbidden", message: "no" });
    const r = await getAuditRecent("o", "a", "b");
    expect(r.kind).toBe("forbidden");
    expect(apiGet).toHaveBeenCalledTimes(1);
  });

  it("flags truncation when even the fallback walk can't reach the end", async () => {
    apiGet.mockResolvedValueOnce(page({ events: [ev(1)], next_after_id: 1 }));
    for (let i = 0; i < 3; i++) apiGet.mockResolvedValueOnce(page({ events: [ev(i + 1)], next_after_id: i + 2 }));
    const r = await getAuditRecent("o", "a", "b");
    expect(r.kind === "ok" && r.data.truncated).toBe(true);
  });

  it("reports a broken chain from the desc reply", async () => {
    apiGet.mockResolvedValueOnce(page({ order: "desc", events: [ev(3)], chain_intact: false, first_broken_id: 3 }));
    const r = await getAuditRecent("o", "a", "b");
    expect(r.kind === "ok" && r.data.chain_intact).toBe(false);
  });
});
