/**
 * SAMPLE DATA MODE: development only, off by default.
 *
 * Turned on by `NEXT_PUBLIC_DEMO_DATA=1` and never in a production build. When on, reads of `/v1/orgs/...` are answered
 * from a synthetic organisation generated here instead of the backend, so the whole console can be exercised (and its
 * pages rendered in tests) without a database. Every page shows a permanent banner saying so, writes are refused, and
 * nothing here is, or looks like, a real organisation: providers are called "demo-provider-*", people "Sample Person N".
 *
 * The data is built from one set of (day, person, model) groups and aggregated up, so the organisation totals, the
 * per-person totals and the cost components agree to the micro-dollar, exactly as the real ledger's views do. It is
 * deterministic (seeded), except that "today" moves, so the window always ends on the current UTC day.
 *
 * The real backend computes latency percentiles from raw calls; here they are synthetic, and the merged summary uses a
 * call-weighted blend because there are no raw calls. That is acceptable only because this is not real data.
 */
import type { ApiState } from "@/lib/api";
import type { CallRow, DenialEvent } from "@/lib/org-api";

export const DEMO_ORG_ID = "demo-organisation";

export function demoEnabled(): boolean {
  return process.env.NODE_ENV !== "production" && process.env.NEXT_PUBLIC_DEMO_DATA === "1";
}

function mulberry32(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Whole micro-dollars -> the 6-decimal string the backend sends. */
export const microsToString = (n: number): string => `${Math.floor(n / 1e6)}.${String(n % 1e6).padStart(6, "0")}`;

interface DemoModel { provider: string; model: string; tier: string | null; priceIn: number; priceOut: number; priceRead: number; priceWrite: number; p50: number; cache: boolean; weight: number }
// Prices are micro-dollars per million tokens.
const MODELS: DemoModel[] = [
  { provider: "demo-provider-a", model: "demo-flagship", tier: "flagship", priceIn: 3_000_000, priceOut: 15_000_000, priceRead: 300_000, priceWrite: 3_750_000, p50: 1900, cache: true, weight: 1 },
  { provider: "demo-provider-a", model: "demo-light", tier: "light", priceIn: 800_000, priceOut: 4_000_000, priceRead: 80_000, priceWrite: 1_000_000, p50: 650, cache: true, weight: 1.6 },
  { provider: "demo-provider-b", model: "demo-open", tier: null, priceIn: 250_000, priceOut: 1_000_000, priceRead: 0, priceWrite: 0, p50: 1150, cache: false, weight: 0.7 },
];
const PEOPLE = 8;
const USER_WEIGHT = [5, 3, 3, 2, 2, 1, 1, 0.4];
const USER_ACTIVITY = [0.95, 0.9, 0.85, 0.8, 0.7, 0.5, 0.35, 0.12];
const subjectOf = (u: number) => `demo-user-${u + 1}`;

interface Group {
  day: string; user: number; m: number; calls: number; failed: number;
  tIn: number; tRead: number; tWrite: number; tOut: number;
  cIn: number; cOut: number; cRead: number; cWrite: number; cOther: number; upper: number;
}

interface Base { today: string; days: string[]; groups: Group[] }
let cache: Base | null = null;

function build(): Base {
  const now = new Date();
  const todayMs = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  const today = new Date(todayMs).toISOString().slice(0, 10);
  if (cache && cache.today === today) return cache;
  const rnd = mulberry32(20261014);
  const groups: Group[] = [];
  const days: string[] = [];
  for (let back = 59; back >= 0; back--) {
    const date = new Date(todayMs - back * 864e5);
    const day = date.toISOString().slice(0, 10);
    days.push(day);
    const weekend = date.getUTCDay() === 0 || date.getUTCDay() === 6;
    const spike = back === 12 ? 3.4 : 1; // one unusually busy day, so the spike flag has something to find
    for (let u = 0; u < PEOPLE; u++) {
      if (rnd() >= (weekend ? 0.15 : USER_ACTIVITY[u])) continue;
      for (let m = 0; m < MODELS.length; m++) {
        const md = MODELS[m];
        let calls = Math.round(USER_WEIGHT[u] * md.weight * spike * (0.6 + rnd() * 0.8) * 6);
        const unpriced = back === 20 && m === 2 && u === 0; // a couple of calls whose cost could not be split
        if (unpriced) calls = Math.max(calls, 3);
        if (calls <= 0) continue;
        const rate = back === 5 ? 0.14 : 0.025; // a bad day, so failures show up in the trend
        const failed = Math.min(calls, Math.round(calls * rate + (rnd() < 0.2 ? 1 : 0)));
        const settled = calls - failed;
        const tIn = settled * Math.round(700 + rnd() * 300);
        const tOut = settled * Math.round(400 + rnd() * 300);
        const tRead = md.cache ? settled * Math.round(5000 + rnd() * 3000) : 0;
        const tWrite = md.cache ? settled * Math.round(300 + rnd() * 200) : 0;
        groups.push({
          day, user: u, m, calls, failed, tIn, tRead, tWrite, tOut,
          cIn: Math.floor((tIn * md.priceIn) / 1e6), cOut: Math.floor((tOut * md.priceOut) / 1e6),
          cRead: Math.floor((tRead * md.priceRead) / 1e6), cWrite: Math.floor((tWrite * md.priceWrite) / 1e6),
          cOther: unpriced ? 1_500_000 : 0, upper: unpriced ? 2 : 0,
        });
      }
    }
  }
  cache = { today, days, groups };
  return cache;
}

const cost = (g: Pick<Group, "cIn" | "cOut" | "cRead" | "cWrite" | "cOther">) => g.cIn + g.cOut + g.cRead + g.cWrite + g.cOther;
const inRange = (day: string, since: string, until: string) => day >= since.slice(0, 10) && day < until.slice(0, 10);

function costFields(g: Group) {
  return {
    cost_usd: microsToString(cost(g)), cost_input_usd: microsToString(g.cIn), cost_output_usd: microsToString(g.cOut),
    cost_cache_read_usd: microsToString(g.cRead), cost_cache_write_usd: microsToString(g.cWrite), cost_unattributed_usd: microsToString(g.cOther),
  };
}

function add(a: Group, b: Group): Group {
  return {
    ...a, calls: a.calls + b.calls, failed: a.failed + b.failed, tIn: a.tIn + b.tIn, tRead: a.tRead + b.tRead, tWrite: a.tWrite + b.tWrite, tOut: a.tOut + b.tOut,
    cIn: a.cIn + b.cIn, cOut: a.cOut + b.cOut, cRead: a.cRead + b.cRead, cWrite: a.cWrite + b.cWrite, cOther: a.cOther + b.cOther, upper: a.upper + b.upper,
  };
}

function groupBy(gs: Group[], key: (g: Group) => string): Map<string, Group> {
  const out = new Map<string, Group>();
  for (const g of gs) { const k = key(g); const cur = out.get(k); out.set(k, cur ? add(cur, g) : { ...g }); }
  return out;
}

// ---------------------------------------------------------------- the endpoints

function usage(b: Base, since: string, until: string) {
  const rows = [...groupBy(b.groups.filter((g) => inRange(g.day, since, until)), (g) => `${g.day}|${g.m}`).values()].sort((x, y) => x.day.localeCompare(y.day) || x.m - y.m);
  return {
    organization_id: DEMO_ORG_ID,
    rows: rows.map((g) => ({
      organization_id: DEMO_ORG_ID, day: g.day, provider: MODELS[g.m].provider, model: MODELS[g.m].model, tool: "call_model",
      calls: g.calls, succeeded: g.calls - g.failed, failed: g.failed,
      tokens_input_fresh: g.tIn, tokens_cache_read: g.tRead, tokens_cache_write: g.tWrite, tokens_output: g.tOut,
      ...costFields(g), upper_bound_calls: g.upper,
    })),
  };
}

function usageByUser(b: Base, since: string, until: string) {
  const rows = [...groupBy(b.groups.filter((g) => inRange(g.day, since, until)), (g) => `${g.day}|${g.user}`).values()].sort((x, y) => x.day.localeCompare(y.day) || x.user - y.user);
  return {
    organization_id: DEMO_ORG_ID,
    rows: rows.map((g) => ({
      organization_id: DEMO_ORG_ID, actor_subject: subjectOf(g.user), day: g.day, calls: g.calls, failed: g.failed,
      tokens_input_fresh: g.tIn, tokens_cache_read: g.tRead, tokens_cache_write: g.tWrite, tokens_output: g.tOut, ...costFields(g), upper_bound_calls: g.upper,
    })),
  };
}

/** Deterministic per-row noise so repeated reads agree. */
const wobble = (day: string, m: number, k: number) => 0.85 + (mulberry32(Number(day.split("-").join("")) + m * 131 + k * 17)() * 0.3);

function performanceDaily(b: Base, since: string, until: string) {
  const rows = [...groupBy(b.groups.filter((g) => inRange(g.day, since, until)), (g) => `${g.day}|${g.m}`).values()].sort((x, y) => x.day.localeCompare(y.day) || x.m - y.m);
  return rows.map((g) => {
    const md = MODELS[g.m];
    const settled = g.calls - g.failed;
    const p50 = md.p50 * wobble(g.day, g.m, 1);
    const gate50 = 5 * wobble(g.day, g.m, 2);
    const reporting = md.cache ? settled : 0;
    const hits = md.cache ? Math.round(settled * 0.72 * wobble(g.day, g.m, 3)) : 0;
    const read = g.tRead, total = g.tIn + g.tRead + g.tWrite;
    return {
      organization_id: DEMO_ORG_ID, day: g.day, provider: md.provider, model: md.model, tool: "call_model", tier: md.tier, calls: settled,
      provider_p50_ms: Math.round(p50), provider_p95_ms: Math.round(p50 * 2.1), provider_p99_ms: Math.round(p50 * 3.4),
      gate_p50_ms: +gate50.toFixed(1), gate_p95_ms: +(gate50 * 2.6).toFixed(1), gate_p99_ms: +(gate50 * 4.4).toFixed(1),
      gate_time_share: +(gate50 / (gate50 + p50)).toFixed(4),
      calls_reporting_cache: md.cache ? reporting : null, calls_with_cache_hit: md.cache ? hits : null,
      cache_hit_rate_requests: md.cache && reporting > 0 ? +(hits / reporting).toFixed(4) : null,
      cache_hit_rate_tokens: md.cache && total > 0 ? +(read / total).toFixed(4) : null,
    };
  });
}

function performanceSummary(b: Base, since: string, until: string, groupByKey: string) {
  const rows = performanceDaily(b, since, until);
  const keyOf = (r: (typeof rows)[number]): string | null =>
    groupByKey === "model" ? r.model : groupByKey === "provider" ? r.provider : groupByKey === "tool" ? r.tool : groupByKey === "tier" ? r.tier : "all";
  const buckets = new Map<string | null, typeof rows>();
  for (const r of rows) { const k = keyOf(r); (buckets.get(k) ?? buckets.set(k, []).get(k)!).push(r); }
  const wavg = (rs: typeof rows, f: (r: (typeof rows)[number]) => number) => { const n = rs.reduce((s, r) => s + r.calls, 0); return n ? rs.reduce((s, r) => s + f(r) * r.calls, 0) / n : null; };
  return {
    organization_id: DEMO_ORG_ID, group_by: groupByKey,
    rows: [...buckets.entries()].sort((x, y) => String(x[0]).localeCompare(String(y[0]))).map(([key, rs]) => {
      const reporting = rs.reduce((s, r) => s + (r.calls_reporting_cache ?? 0), 0);
      const hits = rs.reduce((s, r) => s + (r.calls_with_cache_hit ?? 0), 0);
      const tokRate = wavg(rs.filter((r) => r.cache_hit_rate_tokens != null), (r) => r.cache_hit_rate_tokens as number);
      return {
        key, calls: rs.reduce((s, r) => s + r.calls, 0),
        provider_p50_ms: wavg(rs, (r) => r.provider_p50_ms), provider_p95_ms: wavg(rs, (r) => r.provider_p95_ms), provider_p99_ms: wavg(rs, (r) => r.provider_p99_ms),
        gate_p50_ms: wavg(rs, (r) => r.gate_p50_ms), gate_p95_ms: wavg(rs, (r) => r.gate_p95_ms), gate_p99_ms: wavg(rs, (r) => r.gate_p99_ms),
        gate_time_share: wavg(rs, (r) => r.gate_time_share),
        calls_reporting_cache: reporting, cache_hit_rate_requests: reporting > 0 ? hits / reporting : null, cache_hit_rate_tokens: reporting > 0 ? tokRate : null,
      };
    }),
  };
}

const members = () => ({
  organization_id: DEMO_ORG_ID,
  members: Array.from({ length: PEOPLE }, (_, u) => ({
    user_id: `demo-uuid-${u + 1}`, subject: subjectOf(u), display_name: `Sample Person ${u + 1}`, email: `person.${u + 1}@example.invalid`,
    is_active: true, roles: u === 0 ? ["owner"] : u === 1 ? ["admin"] : ["member"], member_since: "2026-01-05T09:00:00Z",
  })),
});

function budgetMonthly(b: Base) {
  const month = b.today.slice(0, 7);
  const used = b.groups.filter((g) => g.day.startsWith(month)).reduce((s, g) => s + cost(g), 0);
  const all = b.groups.reduce((s, g) => s + cost(g), 0);
  const perDay = all / b.days.length;
  const daysInMonth = new Date(Date.UTC(+month.slice(0, 4), +month.slice(5, 7), 0)).getUTCDate();
  const budget = Math.max(50, Math.ceil((perDay * daysInMonth * 1.3) / 1e6 / 50) * 50) * 1e6; // sized so the month sits well inside it
  return { used, budget };
}

function policy(b: Base) {
  const { budget } = budgetMonthly(b);
  return {
    organization_id: DEMO_ORG_ID, kill_switch: false, kill_switch_reason: null,
    allowed_providers: ["demo-provider-a", "demo-provider-b"], allowed_models: MODELS.map((m) => m.model), allowed_tools: ["call_model"], allowed_data_classes: ["public", "internal"],
    monthly_budget_usd: microsToString(budget), per_user_daily_budget_usd: "25.000000", version: 3,
  };
}

function budget(b: Base) {
  const { used, budget: cap } = budgetMonthly(b);
  const held = 800_000;
  const today = [...groupBy(b.groups.filter((g) => g.day === b.today), (g) => String(g.user)).values()];
  const dayCap = 25_000_000;
  return {
    policy_version: 3, kill_switch: false, month_start: `${b.today.slice(0, 7)}-01T00:00:00+00:00`, day_start: `${b.today}T00:00:00+00:00`,
    monthly: { budget_usd: microsToString(cap), used_usd: microsToString(used), held_usd: microsToString(held), remaining_usd: microsToString(cap - used - held) },
    per_user_daily_budget_usd: microsToString(dayCap),
    users_today: today.map((g) => ({ user_id: subjectOf(g.user), used_usd: microsToString(cost(g)), held_usd: "0.000000", remaining_usd: microsToString(dayCap - cost(g)) })),
  };
}

const REASONS = ["model_not_allowed", "user_daily_budget_exceeded", "data_class_not_allowed", "model_not_allowed", "tool_not_allowed", "user_daily_budget_exceeded"];
function denialEvents(b: Base) {
  const out: (DenialEvent & { created_at: string })[] = [];
  for (let i = 0; i < 24; i++) {
    const back = 58 - Math.floor((i * 58) / 23); // spread over the whole window, newest included
    const day = b.days[b.days.length - 1 - back];
    out.push({
      id: `demo-denial-${i + 1}`, created_at: `${day}T${String(8 + (i % 9)).padStart(2, "0")}:15:00+00:00`, user_id: subjectOf(i % PEOPLE), tool: "call_model", unit: MODELS[i % 3].model,
      provider: MODELS[i % 3].provider, model: MODELS[i % 3].model, data_class: "internal", reason_code: REASONS[i % REASONS.length], detail: "Refused by organisation policy (sample).",
    });
  }
  return out;
}

const hex = (n: number) => Array.from({ length: 64 }, (_, i) => "0123456789abcdef"[(n * 7 + i * 13) % 16]).join("");
function auditEvents(b: Base) {
  const actions = ["org_policy.update", "org_policy.kill_switch_on", "org_policy.kill_switch_off", "org_policy.update", "org_legal_hold.placed", "org_legal_hold.released", "org_policy.update", "org_policy.update"];
  return actions.map((action, i) => ({
    id: i + 1, t_created: `${b.days[Math.max(0, b.days.length - 1 - (actions.length - i) * 3)]}T10:${String(10 + i).padStart(2, "0")}:00+00:00`,
    actor_subject: subjectOf(i % 2), actor_user_id: `demo-uuid-${(i % 2) + 1}`, action, object_type: action.split(".")[0], object_id: DEMO_ORG_ID, tenant_id: DEMO_ORG_ID,
    details: { note: "sample event" }, prev_hash: i === 0 ? null : hex(i), row_hash: hex(i + 1),
  }));
}

function calls(b: Base, q: URLSearchParams) {
  const since = q.get("since") ?? "0000-00-00", until = q.get("until") ?? "9999-12-31";
  const rnd = mulberry32(7);
  const list: CallRow[] = [];
  for (const g of b.groups.filter((x) => inRange(x.day, since, until)).slice(-160)) {
    const md = MODELS[g.m];
    const failed = g.failed > 0 && rnd() < 0.3;
    list.push({
      id: `demo-call-${list.length + 1}`, created_at: `${g.day}T${String(8 + Math.floor(rnd() * 10)).padStart(2, "0")}:${String(Math.floor(rnd() * 60)).padStart(2, "0")}:00+00:00`,
      user_id: subjectOf(g.user), provider: md.provider, model: md.model, scaffold: null, tool: "call_model", tier: md.tier, status: failed ? "failed" : "settled", data_class: "internal",
      tokens_input_fresh: Math.round(g.tIn / Math.max(1, g.calls)), tokens_cache_read: Math.round(g.tRead / Math.max(1, g.calls)), tokens_cache_write: Math.round(g.tWrite / Math.max(1, g.calls)),
      tokens_output: Math.round(g.tOut / Math.max(1, g.calls)), cost_usd: failed ? null : microsToString(Math.round(cost(g) / Math.max(1, g.calls))), cost_source: failed ? null : "declared_price",
      provider_ms: Math.round(md.p50 * (0.8 + rnd() * 0.5)), gate_ms: +(4 + rnd() * 4).toFixed(1), error_type: failed ? "provider_timeout" : null, instance_key: "demo-connection", policy_decision: "allowed",
    });
  }
  const f = (c: CallRow) => (!q.get("model") || c.model === q.get("model")) && (!q.get("tool") || c.tool === q.get("tool")) && (!q.get("status") || c.status === q.get("status")) && (!q.get("user") || c.user_id === q.get("user"));
  return { calls: list.filter(f), next_after: null };
}

function ok(data: unknown): ApiState<never> { return { kind: "ok", data } as ApiState<never>; }
const notInDemo = (path: string): ApiState<never> => ({ kind: "error", message: `Not part of the sample data (${path.split("?")[0]}).`, status: 404 }) as ApiState<never>;

/** Answers a GET the way the backend would, from the synthetic organisation. Anything else is an honest "not in the demo". */
export function demoResponse(path: string): ApiState<never> {
  const url = new URL(path, "http://demo.invalid");
  const p = url.pathname;
  const q = url.searchParams;
  const b = build();
  const since = q.get("since") ?? "0000-00-00", until = q.get("until") ?? "9999-12-31";
  if (p === "/v1/orgs/mine") return ok([{ organization_id: DEMO_ORG_ID, name: "Demo Organisation (sample data)", role: "owner", roles: ["owner"] }]);
  const m = /^\/v1\/orgs\/([^/]+)\/(.+)$/.exec(p);
  if (!m) return notInDemo(p);
  const route = m[2];
  if (route === "usage") return ok(usage(b, since, until));
  if (route === "usage/users") return ok(usageByUser(b, since, until));
  if (route === "performance") return ok({ organization_id: DEMO_ORG_ID, rows: performanceDaily(b, since, until) });
  if (route === "performance/summary") return ok(performanceSummary(b, since, until, q.get("group_by") ?? "total"));
  if (route === "members") return ok(members());
  if (route === "policy") return ok(policy(b));
  if (route === "budget") return ok(budget(b));
  if (route === "calls") return ok(calls(b, q));
  if (route === "denials") {
    const events = denialEvents(b).filter((e) => inRange(e.created_at.slice(0, 10), since, until));
    const counts = new Map<string, number>();
    for (const e of events) counts.set(e.reason_code, (counts.get(e.reason_code) ?? 0) + 1);
    const limit = Math.max(1, Number(q.get("limit") ?? 200));
    return ok({ counts_by_reason: [...counts.entries()].map(([reason_code, count]) => ({ reason_code, count })).sort((x, y) => y.count - x.count), events: events.slice(0, limit), next_after: null });
  }
  if (route === "audit") {
    const evs = auditEvents(b).filter((e) => inRange(e.t_created.slice(0, 10), since, until));
    const limit = Math.max(1, Number(q.get("limit") ?? 1000));
    if (q.get("order") === "desc") return ok({ order: "desc", events: [...evs].reverse().slice(0, limit), next_before_id: null, next_after_id: null, chain_intact: true, first_broken_id: null });
    return ok({ events: evs.slice(0, limit), next_after_id: null, chain_intact: true, first_broken_id: null });
  }
  return notInDemo(p);
}

export const DEMO_WRITE_REFUSAL = "Sample data is read-only: nothing was changed.";
