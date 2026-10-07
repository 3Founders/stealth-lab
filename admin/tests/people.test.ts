import { describe, expect, it } from "vitest";
import { toMicros } from "@/lib/money";
import { capStatus, displayName, totalsByUser, userBudgets } from "@/lib/people";
import type { BudgetPosition, Member, UserUsageRow } from "@/lib/org-api";

const urow = (over: Partial<UserUsageRow> = {}): UserUsageRow => ({
  actor_subject: "u1", day: "2026-09-10", calls: 2, failed: 0, tokens_input_fresh: 100, tokens_cache_read: 50, tokens_cache_write: 10, tokens_output: 40,
  cost_usd: "0.30", cost_input_usd: "0.10", cost_output_usd: "0.15", cost_cache_read_usd: "0.03", cost_cache_write_usd: "0.02", upper_bound_calls: 0, ...over,
});
const member = (over: Partial<Member> = {}): Member => ({ user_id: "u1", subject: "u1", display_name: "Ada", email: "ada@x.io", is_active: true, roles: ["member"], member_since: null, ...over });

describe("totalsByUser", () => {
  it("sums exactly per user and orders by spend", () => {
    const t = totalsByUser([urow(), urow({ day: "2026-09-11" }), urow({ actor_subject: "u2", cost_usd: "5" })]);
    expect(t.map((x) => x.subject)).toEqual(["u2", "u1"]);
    const u1 = t[1];
    expect(u1.total).toBe(toMicros("0.60"));
    expect(u1.processing).toBe(toMicros("0.10")); // cache read + write
    expect(u1.input + u1.output + u1.processing).toBe(u1.total);
    expect(u1.calls).toBe(4);
    expect(u1.lastDay).toBe("2026-09-11");
    expect(u1.tokens).toBe(400);
  });
  it("counts unpriced calls", () => {
    expect(totalsByUser([urow({ upper_bound_calls: 3 }), urow({ upper_bound_calls: 1 })])[0].upperBoundCalls).toBe(4);
  });
});

describe("displayName", () => {
  it("prefers display name, then email, then a short id; never invents", () => {
    expect(displayName("u1", [member()])).toBe("Ada");
    expect(displayName("u1", [member({ display_name: null })])).toBe("ada@x.io");
    expect(displayName("u1", [member({ display_name: " ", email: null })])).toBe("u1");
    expect(displayName("123456789abcdef", [])).toBe("12345678…");
    expect(displayName(null, [])).toBe("(system)");
  });
});

describe("capStatus", () => {
  const cap = toMicros("10");
  it("classifies against the per-user daily cap, held reservations included", () => {
    expect(capStatus(toMicros("5"), cap)).toEqual({ state: "ok", pct: 50 });
    expect(capStatus(toMicros("8"), cap).state).toBe("near");
    expect(capStatus(toMicros("10"), cap).state).toBe("at");
    expect(capStatus(toMicros("1"), null).state).toBe("none");
  });
  it("a $0 cap with any spend is at the cap, with no percentage", () => {
    expect(capStatus(toMicros("0.1"), 0n)).toEqual({ state: "at", pct: null });
    expect(capStatus(0n, 0n).state).toBe("none");
  });
});

describe("userBudgets", () => {
  it("adds held to used when judging the cap and sorts heaviest first", () => {
    const b = {
      policy_version: 1, kill_switch: false, month_start: "", day_start: "", per_user_daily_budget_usd: "10",
      monthly: { budget_usd: "100", used_usd: "0", held_usd: "0", remaining_usd: "100" },
      users_today: [{ user_id: "a", used_usd: "1", held_usd: "0", remaining_usd: "9" }, { user_id: "b", used_usd: "7", held_usd: "2", remaining_usd: "1" }],
    } as BudgetPosition;
    const u = userBudgets(b);
    expect(u.map((x) => x.user_id)).toEqual(["b", "a"]);
    expect(u[0].position.state).toBe("near"); // 9/10
  });
});
