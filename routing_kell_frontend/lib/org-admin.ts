/**
 * Organization admin: a typed client for backend/app/api/org_admin.py, which is the source of truth. It is built behind a flag
 * that is OFF by default (NEXT_PUBLIC_ORG_ADMIN=on turns the pages on), and nothing here makes a request until a page that is
 * switched on asks for one.
 *
 * Base URL: the hosted MCP host (the origin of NEXT_PUBLIC_KEL_MCP_URL), with the website-session token as a Bearer and the same
 * CORS list as the chat routes. No cookies. Money arrives as strings (the server renders decimals as text); use `usd()` to show it.
 * The server enforces roles on every route; the pages hide what a role can't use so nobody is offered a button that will be refused.
 * Left out on purpose: legal holds and erasure requests.
 */
import { MCP_ORIGIN } from "@/lib/connector";

export const orgAdminEnabled = (flag: string | undefined = process.env.NEXT_PUBLIC_ORG_ADMIN): boolean => (flag ?? "").trim().toLowerCase() === "on";

export type Role = "owner" | "admin" | "member" | "viewer";
export const ADMIN_ROLES: readonly Role[] = ["owner", "admin"];
/** Everything on the org pages except the connection details is for owners and admins. */
export const canAdmin = (role: Role | undefined): boolean => role === "owner" || role === "admin";

export interface OrgSummary { organization_id: string; name: string; role: Role; roles: Role[] }

export interface OrgPolicy {
  organization_id: string; kill_switch: boolean; kill_switch_reason: string | null;
  allowed_providers: string[]; allowed_models: string[]; allowed_tools: string[]; allowed_data_classes: string[];
  monthly_budget_usd: string; per_user_daily_budget_usd: string; version: number;
}
export type PolicyChanges = Partial<Pick<OrgPolicy, "allowed_providers" | "allowed_models" | "allowed_tools" | "allowed_data_classes" | "monthly_budget_usd" | "per_user_daily_budget_usd">>;

export interface Member { user_id: string; subject: string; display_name: string | null; email: string | null; is_active: boolean; roles: Role[]; member_since: string }
export interface Invite { id: string; label: string | null; created_by: string | null; created_at: string; expires_at: string; max_uses: number; uses: number; revoked_at: string | null; active: boolean }
export interface CreatedInvite { id: string; code: string; expires_at: string; max_uses: number; label: string | null }

export interface Budget {
  policy_version: number; kill_switch: boolean; month_start: string; day_start: string;
  monthly: { budget_usd: string; used_usd: string; held_usd: string; remaining_usd: string };
  per_user_daily_budget_usd: string;
  users_today: Array<{ user_id: string; used_usd: string; held_usd: string; remaining_usd: string }>;
}
export interface UserUsageRow {
  organization_id: string; actor_subject: string; day: string; calls: number; failed: number;
  tokens_input_fresh: number; tokens_cache_read: number; tokens_cache_write: number; tokens_output: number; cost_usd: string; upper_bound_calls: number;
}

export type CallStatus = "reserved" | "settled" | "failed";
export interface CallRow {
  id: string; created_at: string; user_id: string; provider: string; model: string; scaffold: string | null; tool: string; tier: string | null; status: CallStatus;
  data_class: string; tokens_input_fresh: number | null; tokens_cache_read: number | null; tokens_cache_write: number | null; tokens_output: number | null;
  cost_usd: string | null; cost_source: string | null; provider_ms: number | null; gate_ms: number | null; error_type: string | null; instance_key: string | null; policy_decision: "allowed";
}
export interface CallsPage { calls: CallRow[]; next_after: string | null }
export interface DenialRow { id: string; created_at: string; user_id: string; tool: string; unit: string; provider: string; model: string; data_class: string; reason_code: string; detail: unknown }
export interface DenialsPage { counts_by_reason: Array<{ reason_code: string; count: number }>; events: DenialRow[]; next_after: string | null }
export interface CallFilters { model?: string; tool?: string; status?: CallStatus; user?: string; after?: string; limit?: number }

export interface ProviderUnit { model: string; provider_model?: string; input_per_mtok: number | string; output_per_mtok: number | string }
export interface ProviderKey {
  connection_id: string; provider: string; base_url: string; units: ProviderUnit[]; allowed_data_classes: string[];
  key_hint: string | null; enabled: boolean; updated_by: string | null; updated_at: string | null;
}
export interface ProviderKeyInput {
  provider: string; base_url: string; units: ProviderUnit[]; api_key?: string; allowed_data_classes: string[];
  zdr?: boolean; no_training?: boolean; region?: string; dpa_signed?: boolean;
}

export interface Onboarding {
  organization_id: string; provider_keys: number; policy_set: boolean; kill_switch: boolean; active_invites: number; members: number; members_ready: number;
  next_step: "connect_key" | "set_policy" | "running" | "waiting_for_developers" | "invite_developers";
}
export interface ClientConfig { organization_id: string; name: string; gateway: string; models: string[]; profiles: unknown }
export interface Joined extends Partial<ClientConfig> { organization_id: string }

export interface AuditEvent { id: number; t_created: string; actor_subject: string; actor_user_id: string | null; action: string; object_type: string; object_id: string | null; tenant_id: string | null; details: unknown; prev_hash: string | null; row_hash: string | null }
export interface AuditPage { events: AuditEvent[]; chain_intact: boolean; next_after_id?: number | null; next_before_id?: number | null }

export class OrgApiError extends Error {
  constructor(message: string, readonly status?: number) { super(message); this.name = "OrgApiError"; }
}

export interface OrgDeps { token(): Promise<string | null>; origin?: string; fetchImpl?: typeof fetch }

/** The server answers with `{detail: "..."}` (FastAPI) or `{error: ...}`; both are sentences meant for people. */
function sentence(body: unknown, status: number): string {
  const b = body as { detail?: unknown; error?: unknown } | null;
  const d = b?.detail ?? (typeof b?.error === "string" ? b.error : (b?.error as { message?: unknown } | undefined)?.message);
  if (typeof d === "string" && d) return d;
  if (Array.isArray(d) && d.length) return String((d[0] as { msg?: unknown })?.msg ?? "The request wasn't accepted.");
  return status === 401 ? "Please sign in again." : status === 403 ? "Your role can't do that." : status === 404 ? "Not found." : `The server answered ${status}.`;
}

const enc = encodeURIComponent;
function qs(o: Record<string, string | number | undefined>): string {
  const s = new URLSearchParams(Object.entries(o).filter(([, v]) => v !== undefined && v !== "").map(([k, v]) => [k, String(v)])).toString();
  return s ? `?${s}` : "";
}

export function orgApi(deps: OrgDeps) {
  const base = (deps.origin ?? MCP_ORIGIN).replace(/\/+$/, "");
  const doFetch = deps.fetchImpl ?? fetch;

  async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
    const token = await deps.token();
    if (!token) throw new OrgApiError("Please sign in again.", 401);
    let res: Response;
    try {
      res = await doFetch(`${base}${path}`, {
        method, cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer",
        headers: { authorization: `Bearer ${token}`, accept: "application/json", ...(body !== undefined ? { "content-type": "application/json" } : {}) },
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      });
    } catch { throw new OrgApiError("Couldn't reach the keळ server. Check your connection and try again."); }
    if (res.status === 204) return undefined as T;
    let json: unknown = null;
    try { json = await res.json(); } catch { /* no body */ }
    if (!res.ok) throw new OrgApiError(sentence(json, res.status), res.status);
    return json as T;
  }
  const o = (org: string) => `/v1/orgs/${enc(org)}`;

  return {
    mine: () => call<OrgSummary[]>("GET", "/v1/orgs/mine"),
    policy: (org: string) => call<OrgPolicy>("GET", `${o(org)}/policy`),
    putPolicy: (org: string, policy: PolicyChanges, expectedVersion?: number) =>
      call<OrgPolicy>("PUT", `${o(org)}/policy`, { policy, ...(expectedVersion !== undefined ? { expected_version: expectedVersion } : {}) }),
    recommendedPolicy: (org: string, monthly: string, perUserDaily: string) =>
      call<OrgPolicy>("POST", `${o(org)}/policy/recommended`, { monthly_budget_usd: monthly, per_user_daily_budget_usd: perUserDaily }),
    killSwitch: (org: string, on: boolean, reason: string) => call<OrgPolicy>("POST", `${o(org)}/kill-switch`, { on, reason }),

    members: async (org: string) => (await call<{ members: Member[] }>("GET", `${o(org)}/members`)).members,
    invites: (org: string) => call<Invite[]>("GET", `${o(org)}/invites`),
    createInvite: (org: string, v: { label?: string; days?: number; max_uses?: number }) => call<CreatedInvite>("POST", `${o(org)}/invites`, v),
    revokeInvite: (org: string, id: string) => call<void>("DELETE", `${o(org)}/invites/${enc(id)}`),
    join: (code: string) => call<Joined>("POST", "/v1/join", { code }),

    budget: (org: string) => call<Budget>("GET", `${o(org)}/budget`),
    usageByUser: async (org: string, since: string, until: string) => (await call<{ rows: UserUsageRow[] }>("GET", `${o(org)}/usage/users${qs({ since, until })}`)).rows,
    calls: (org: string, since: string, until: string, f: CallFilters = {}) => call<CallsPage>("GET", `${o(org)}/calls${qs({ since, until, ...f })}`),
    denials: (org: string, since: string, until: string, after?: string, limit?: number) => call<DenialsPage>("GET", `${o(org)}/denials${qs({ since, until, after, limit })}`),

    providerKeys: (org: string) => call<ProviderKey[]>("GET", `${o(org)}/provider-keys`),
    putProviderKey: (org: string, connectionId: string, v: ProviderKeyInput) => call<ProviderKey>("PUT", `${o(org)}/provider-keys/${enc(connectionId)}`, v),
    removeProviderKey: (org: string, connectionId: string) => call<void>("DELETE", `${o(org)}/provider-keys/${enc(connectionId)}`),

    onboarding: (org: string) => call<Onboarding>("GET", `${o(org)}/onboarding`),
    clientConfig: (org: string) => call<ClientConfig>("GET", `${o(org)}/client-config`),
    audit: (org: string, since: string, until: string, o2: { order?: "asc" | "desc"; limit?: number; before_id?: number; after_id?: number } = {}) =>
      call<AuditPage>("GET", `${o(org)}/audit${qs({ since, until, format: "json", ...o2 })}`),
  };
}
export type OrgApi = ReturnType<typeof orgApi>;

// ---- small pure helpers the pages use

/** "12.5" -> "$12.50". Amounts are the organization's own spend and budgets, shown as USD. */
export function usd(v: string | number | null | undefined): string {
  const n = Number(v);
  if (v === null || v === undefined || v === "" || !Number.isFinite(n)) return "–";
  return `$${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: n !== 0 && Math.abs(n) < 0.01 ? 4 : 2 })}`;
}

/** The share of a budget used, 0 to 100, or null when there is no budget to measure against. */
export function usedShare(used: string | number, held: string | number, budget: string | number): number | null {
  const b = Number(budget);
  if (!(b > 0)) return null;
  return Math.max(0, Math.min(100, ((Number(used) + Number(held)) / b) * 100));
}

/** [first of this UTC month, tomorrow) as ISO dates: the range "this month so far". `until` is exclusive on the server. */
export function monthRange(now: Date = new Date()): { since: string; until: string } {
  const since = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
  const until = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1));
  return { since: since.toISOString().slice(0, 10), until: until.toISOString().slice(0, 10) };
}
export function lastDays(days: number, now: Date = new Date()): { since: string; until: string } {
  const until = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1));
  const since = new Date(until.getTime() - days * 86_400_000);
  return { since: since.toISOString().slice(0, 10), until: until.toISOString().slice(0, 10) };
}

/** Per-person totals over the rows the server returns per person per day, biggest spender first. */
export function totalsByUser(rows: UserUsageRow[]): Array<{ user: string; calls: number; failed: number; tokens: number; cost: number }> {
  const by = new Map<string, { user: string; calls: number; failed: number; tokens: number; cost: number }>();
  for (const r of rows) {
    const t = by.get(r.actor_subject) ?? { user: r.actor_subject, calls: 0, failed: 0, tokens: 0, cost: 0 };
    t.calls += Number(r.calls); t.failed += Number(r.failed);
    t.tokens += Number(r.tokens_input_fresh) + Number(r.tokens_cache_read) + Number(r.tokens_cache_write) + Number(r.tokens_output);
    t.cost += Number(r.cost_usd);
    by.set(r.actor_subject, t);
  }
  return [...by.values()].sort((a, b) => b.cost - a.cost || a.user.localeCompare(b.user));
}

/** The name to show for an id in usage, calls and denials: members' `subject` is that id. Falls back to a shortened id. */
export function nameFor(subject: string, members: Member[]): string {
  const m = members.find((x) => x.subject === subject || x.user_id === subject);
  return m?.display_name || m?.email || (subject.length > 12 ? `${subject.slice(0, 8)}…` : subject);
}

// ---- policy: "public data only"
/** The data classes that carry only public material. Choosing "public data only" in the policy sets exactly these. */
export const PUBLIC_DATA_CLASSES = ["PUBLIC_SOURCE", "PUBLIC_DERIVED", "GLOBAL_PROCEDURE"] as const;
export const KNOWN_DATA_CLASSES = [
  { id: "PUBLIC_SOURCE", label: "Public source material", public: true },
  { id: "PUBLIC_DERIVED", label: "Derived from public material", public: true },
  { id: "GLOBAL_PROCEDURE", label: "Shared procedures", public: true },
  { id: "USER_PRIVATE", label: "Private to a person or team", public: false },
] as const;
export const isPublicOnly = (classes: readonly string[]): boolean =>
  classes.length > 0 && classes.every((c) => (PUBLIC_DATA_CLASSES as readonly string[]).includes(c));

export const REASON_TEXT: Record<string, string> = {
  kill_switch: "The organization was stopped", no_policy: "No policy set yet", provider_not_allowed: "Provider not allowed", model_not_allowed: "Model not allowed",
  tool_not_allowed: "Tool not allowed", data_class_not_allowed: "Data type not allowed", unpriced_unit: "Model has no cost set", monthly_budget_zero: "Monthly budget is zero",
  user_daily_budget_zero: "Daily budget is zero", monthly_budget_exceeded: "Monthly budget reached", user_daily_budget_exceeded: "Person's daily budget reached",
};

export const listFromText = (s: string): string[] => [...new Set(s.split(/[\s,]+/).map((x) => x.trim()).filter(Boolean))];
