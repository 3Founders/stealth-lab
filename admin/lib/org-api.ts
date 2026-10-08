/**
 * Client for the per-organisation governance API (`/v1/orgs/...`). Built against the contract the backend
 * governance work documents; that router may not be deployed yet, in which case every call surfaces as an
 * explicit `error` state (404/5xx) rather than a fabricated empty result.
 *
 * Authorization is the backend's: a caller who is not a member gets 404, the wrong role 403. `role` from
 * `/v1/orgs/mine` is only used to decide which pages to *offer*.
 */
import { API_URL, apiDelete, apiGet, apiPost, apiPut, type ApiState } from "@/lib/api";
import { getAccessToken } from "@/lib/session";

/** Roles the backend returns today are owner/admin/member/viewer; team_lead, executive and auditor are planned, so they are
 * typed here and every gate below treats an unknown or planned role as having NO extra rights until it is wired. */
export type OrgRole = "owner" | "admin" | "member" | "viewer" | "team_lead" | "executive" | "auditor";

export interface Org {
  organization_id: string;
  name: string;
  role: OrgRole;
  roles: OrgRole[];
}

export interface OrgPolicy {
  organization_id: string;
  kill_switch: boolean;
  kill_switch_reason: string | null;
  allowed_providers: string[];
  allowed_models: string[];
  allowed_tools: string[];
  allowed_data_classes: string[];
  monthly_budget_usd: string;
  per_user_daily_budget_usd: string;
  version: number;
}

/** The fields PUT /policy accepts. The kill switch is its own endpoint and is never part of a policy write. */
export type PolicyFields = Omit<OrgPolicy, "organization_id" | "kill_switch" | "kill_switch_reason" | "version">;

export interface UsageRow {
  organization_id: string;
  day: string;
  provider: string;
  model: string;
  tool: string;
  calls: number;
  succeeded: number;
  failed: number;
  tokens_input_fresh: number;
  tokens_cache_read: number;
  tokens_cache_write: number;
  tokens_output: number;
  /** Decimal strings in USD. Components + cost_unattributed_usd = cost_usd (to ~1e-6). Absent on an older backend. */
  cost_input_usd?: string | null;
  cost_output_usd?: string | null;
  cost_cache_read_usd?: string | null;
  cost_cache_write_usd?: string | null;
  cost_unattributed_usd?: string | null;
  cost_usd: string | number | null;
  upper_bound_calls: number;
}

/** One row per day x provider x model x tool x tier. Postgres numerics may arrive as numbers or strings; null is meaningful. */
export interface PerformanceRow {
  organization_id?: string;
  day: string;
  provider: string;
  model: string;
  tool: string;
  tier: string | null;
  calls: number | string;
  provider_p50_ms: number | string | null;
  provider_p95_ms: number | string | null;
  provider_p99_ms: number | string | null;
  /** Router overhead: time our code spends before the provider is called. */
  gate_p50_ms: number | string | null;
  gate_p95_ms: number | string | null;
  gate_p99_ms: number | string | null;
  gate_time_share: number | string | null;
  calls_reporting_cache: number | string | null;
  calls_with_cache_hit: number | string | null;
  /** NULL = the provider did not report cache tokens. It is NOT 0%. */
  cache_hit_rate_requests: number | string | null;
  cache_hit_rate_tokens: number | string | null;
}

/** Spend per user per day (decimal strings in USD). actor_subject is the identity-provider user id, not an email. */
export interface UserUsageRow {
  organization_id?: string;
  actor_subject: string;
  day: string;
  calls: number;
  failed: number;
  tokens_input_fresh: number;
  tokens_cache_read: number;
  tokens_cache_write: number;
  tokens_output: number;
  cost_usd: string | null;
  cost_input_usd?: string | null;
  cost_output_usd?: string | null;
  cost_cache_read_usd?: string | null;
  cost_cache_write_usd?: string | null;
  upper_bound_calls: number;
}

/** Live budget position. used = settled spend; held = live reservations; remaining = budget - used - held. */
export interface BudgetPosition {
  policy_version: number;
  kill_switch: boolean;
  month_start: string;
  day_start: string;
  monthly: { budget_usd: string; used_usd: string; held_usd: string; remaining_usd: string };
  per_user_daily_budget_usd: string;
  users_today: { user_id: string; used_usd: string; held_usd: string; remaining_usd: string }[];
}

export interface Member {
  user_id: string;
  /** Equals user_id / actor_subject everywhere else: join on this to show names. */
  subject: string;
  display_name: string | null;
  email: string | null;
  is_active: boolean;
  roles: OrgRole[];
  member_since: string | null;
}

export interface CallRow {
  id: string;
  created_at: string;
  user_id: string | null;
  provider: string;
  model: string;
  scaffold?: string | null;
  tool: string;
  tier: string | null;
  status: "reserved" | "settled" | "failed" | string;
  data_class: string | null;
  tokens_input_fresh: number;
  tokens_cache_read: number;
  tokens_cache_write: number;
  tokens_output: number;
  cost_usd: string | null;
  cost_source: string | null;
  provider_ms: number | string | null;
  gate_ms: number | string | null;
  error_type: string | null;
  instance_key?: string | null;
  policy_decision: string;
}

export interface DenialEvent {
  id: string | number;
  created_at: string;
  user_id: string | null;
  tool: string | null;
  unit: string | null;
  provider: string | null;
  model: string | null;
  data_class: string | null;
  reason_code: string;
  detail: unknown;
}

/** One group of the merged latency summary. Percentiles come from the raw calls over the whole range, never averaged. */
export interface PerfSummaryRow {
  key: string | null;
  calls: number | string;
  provider_p50_ms: number | string | null;
  provider_p95_ms: number | string | null;
  provider_p99_ms: number | string | null;
  gate_p50_ms: number | string | null;
  gate_p95_ms: number | string | null;
  gate_p99_ms: number | string | null;
  gate_time_share: number | string | null;
  calls_reporting_cache: number | string | null;
  cache_hit_rate_requests: number | string | null;
  cache_hit_rate_tokens: number | string | null;
}
export type PerfGroupBy = "total" | "model" | "provider" | "tool" | "tier";

export interface AuditEvent {
  id: number;
  t_created: string;
  actor_subject: string | null;
  actor_user_id: string | null;
  action: string;
  object_type: string | null;
  object_id: string | null;
  tenant_id: string | null;
  details: unknown;
  prev_hash: string | null;
  row_hash: string | null;
}

export interface AuditPage {
  events: AuditEvent[];
  /** Present only when the backend supports `order=desc` (then "asc" or "desc"). Absent means an older backend. */
  order?: "asc" | "desc";
  next_before_id?: number | null;
  next_after_id: number | null;
  chain_intact: boolean;
  first_broken_id: number | null;
}

export interface ErasureResult {
  request_id: string;
  completed: boolean;
  manifest: unknown;
  blocked: unknown[];
}

const o = (orgId: string) => `/v1/orgs/${encodeURIComponent(orgId)}`;

export const getMyOrgs = (signal?: AbortSignal) => apiGet<Org[]>("/v1/orgs/mine", signal);
export const getPolicy = (orgId: string, signal?: AbortSignal) => apiGet<OrgPolicy>(`${o(orgId)}/policy`, signal);

export const putPolicy = (orgId: string, policy: PolicyFields, expectedVersion?: number) =>
  apiPut<OrgPolicy>(`${o(orgId)}/policy`, { expected_version: expectedVersion ?? null, policy });

export const setKillSwitch = (orgId: string, on: boolean, reason: string) =>
  apiPost<OrgPolicy>(`${o(orgId)}/kill-switch`, { on, reason });

export const getUsage = (orgId: string, since: string, until: string, signal?: AbortSignal) =>
  apiGet<{ organization_id: string; rows: UsageRow[] }>(`${o(orgId)}/usage?${new URLSearchParams({ since, until })}`, signal);

export const getPerformance = (orgId: string, since: string, until: string, signal?: AbortSignal) =>
  apiGet<{ organization_id: string; rows: PerformanceRow[] }>(`${o(orgId)}/performance?${new URLSearchParams({ since, until })}`, signal);

export const getUsageByUser = (orgId: string, since: string, until: string, signal?: AbortSignal) =>
  apiGet<{ rows: UserUsageRow[] }>(`${o(orgId)}/usage/users?${new URLSearchParams({ since, until })}`, signal);

/** 409 until the organisation has a policy (no defaults exist). */
export const getBudget = (orgId: string, signal?: AbortSignal) => apiGet<BudgetPosition>(`${o(orgId)}/budget`, signal);
export const getMembers = (orgId: string, signal?: AbortSignal) => apiGet<{ members: Member[] }>(`${o(orgId)}/members`, signal);

export interface CallFilters { model?: string; tool?: string; status?: string; user?: string }
export function getCalls(orgId: string, since: string, until: string, f: CallFilters = {}, after?: string, limit = 200, signal?: AbortSignal) {
  const q = new URLSearchParams({ since, until, limit: String(limit) });
  for (const [k, v] of Object.entries(f)) if (v) q.set(k, v);
  if (after) q.set("after", after);
  return apiGet<{ calls: CallRow[]; next_after: string | null }>(`${o(orgId)}/calls?${q}`, signal);
}

export function getDenials(orgId: string, since: string, until: string, after?: string, limit = 200, signal?: AbortSignal) {
  const q = new URLSearchParams({ since, until, limit: String(limit) });
  if (after) q.set("after", after);
  return apiGet<{ counts_by_reason: { reason_code: string; count: number }[]; events: DenialEvent[]; next_after: string | null }>(`${o(orgId)}/denials?${q}`, signal);
}

export const getPerformanceSummary = (orgId: string, since: string, until: string, groupBy: PerfGroupBy, signal?: AbortSignal) =>
  apiGet<{ group_by: PerfGroupBy; rows: PerfSummaryRow[] }>(`${o(orgId)}/performance/summary?${new URLSearchParams({ since, until, group_by: groupBy })}`, signal);

export const getAudit = (orgId: string, since: string, until: string, afterId = 0, limit = 200, signal?: AbortSignal) =>
  apiGet<AuditPage>(`${o(orgId)}/audit?${new URLSearchParams({ since, until, after_id: String(afterId), limit: String(limit) })}`, signal);

/**
 * The newest audit events in a range. The audit API returns oldest first, so "recent" means walking the pages to the end;
 * this stops after `maxPages` and says so (`truncated`), in which case what is returned is the OLDEST part of the range,
 * not the latest, and the caller must not present it as "recent".
 */
export async function getAuditTail(orgId: string, since: string, until: string, maxPages = 3, signal?: AbortSignal):
  Promise<ApiState<{ events: AuditEvent[]; truncated: boolean; chain_intact: boolean; first_broken_id: number | null }>> {
  let after = 0;
  let events: AuditEvent[] = [];
  let intact = true;
  let broken: number | null = null;
  for (let page = 0; page < maxPages; page++) {
    const r = await getAudit(orgId, since, until, after, 1000, signal);
    if (r.kind !== "ok") return r;
    events = events.concat(r.data.events);
    if (!r.data.chain_intact) { intact = false; broken = broken ?? r.data.first_broken_id; }
    if (r.data.next_after_id == null) return { kind: "ok", data: { events, truncated: false, chain_intact: intact, first_broken_id: broken } };
    after = r.data.next_after_id;
  }
  return { kind: "ok", data: { events, truncated: true, chain_intact: intact, first_broken_id: broken } };
}

/** The latest audit events, newest first, however the backend can supply them. */
export interface AuditRecent { events: AuditEvent[]; chain_intact: boolean; first_broken_id: number | null; checked: number; newestFirst: boolean; truncated: boolean }

/**
 * Prefer `order=desc` (one small request). A backend that predates it ignores the parameter and answers oldest-first
 * without an `order` key, or refuses it with a 422: in both cases fall back to walking the pages (getAuditTail) so the
 * result is still the true latest events, or is flagged `truncated` when even that can't reach them.
 */
export async function getAuditRecent(orgId: string, since: string, until: string, take = 50, signal?: AbortSignal): Promise<ApiState<AuditRecent>> {
  const q = new URLSearchParams({ since, until, order: "desc", limit: String(take) });
  const r = await apiGet<AuditPage>(`${o(orgId)}/audit?${q}`, signal);
  if (r.kind === "ok" && r.data.order === "desc") {
    return { kind: "ok", data: { events: r.data.events, chain_intact: r.data.chain_intact, first_broken_id: r.data.first_broken_id, checked: r.data.events.length, newestFirst: true, truncated: false } };
  }
  if (r.kind === "unauthenticated" || r.kind === "forbidden" || r.kind === "unconfigured") return r;
  const walked = await getAuditTail(orgId, since, until, 3, signal);
  if (walked.kind !== "ok") return walked;
  return { kind: "ok", data: { events: walked.data.events, chain_intact: walked.data.chain_intact, first_broken_id: walked.data.first_broken_id, checked: walked.data.events.length, newestFirst: false, truncated: walked.data.truncated } };
}

/** The CSV export needs the bearer token, so it is fetched and saved as a Blob rather than linked to. */
export async function downloadAuditCsv(orgId: string, since: string, until: string): Promise<{ ok: boolean; message: string; chainIntact?: boolean }> {
  if (!API_URL) return { ok: false, message: "No backend configured." };
  const token = await getAccessToken();
  if (!token) return { ok: false, message: "Sign in first." };
  try {
    const q = new URLSearchParams({ since, until, format: "csv", limit: "10000" });
    const res = await fetch(`${API_URL}${o(orgId)}/audit?${q}`, { headers: { Authorization: `Bearer ${token}` } });
    if (!res.ok) return { ok: false, message: res.status === 403 ? "Only an admin or owner can export the audit log." : `The backend answered ${res.status}.` };
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `audit-${since.slice(0, 10)}-to-${until.slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
    const chainIntact = res.headers.get("X-Chain-Intact") === "true";
    const more = res.headers.get("X-Next-After-Id");
    return { ok: true, chainIntact, message: more ? "Downloaded the first 10,000 events; the range has more (narrow the dates to export the rest)." : "Downloaded." };
  } catch {
    return { ok: false, message: "Could not reach the backend." };
  }
}

export const placeLegalHold = (orgId: string, reason: string) => apiPost<{ id?: string } & Record<string, unknown>>(`${o(orgId)}/legal-holds`, { reason });
export const releaseLegalHold = (orgId: string, holdId: string) => apiDelete<void>(`${o(orgId)}/legal-holds/${encodeURIComponent(holdId)}`);
export const requestErasure = (orgId: string, reason: string) => apiPost<{ id?: string } & Record<string, unknown>>(`${o(orgId)}/erasure-requests`, { reason });
export const approveErasure = (orgId: string, requestId: string) => apiPost<Record<string, unknown>>(`${o(orgId)}/erasure-requests/${encodeURIComponent(requestId)}/approve`, {});
export const executeErasure = (orgId: string, requestId: string) => apiPost<ErasureResult>(`${o(orgId)}/erasure-requests/${encodeURIComponent(requestId)}/execute`, {});

export const isAdminRole = (role?: OrgRole) => role === "owner" || role === "admin";
/** Who may see individuals (per-person spend, calls by person, who was refused). Executives see aggregates only: people
 * fear usage data feeding performance reviews, so identity-level views are limited to owner, admin and (later) team lead. */
export const canSeePerPerson = (role?: OrgRole) => role === "owner" || role === "admin" || role === "team_lead";
export const isOwnerRole = (role?: OrgRole) => role === "owner";

/** Splits a textarea/comma list into a trimmed, de-duplicated list (no wildcards are ever invented here). */
export function parseList(text: string): string[] {
  return [...new Set(text.split(/[\n,]/).map((s) => s.trim()).filter(Boolean))];
}

export type { ApiState };
