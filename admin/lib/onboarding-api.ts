/**
 * Client for the onboarding routes of the per-organisation API: the company's provider keys, the recommended policy,
 * invitations and the "who is ready" checklist (backend/app/api/org_admin.py, services/org_keys.py and org_onboarding.py).
 *
 * The provider key is write-only here: the backend never returns it, only a hint (the last four characters).
 */
import { API_URL, apiDelete, apiGet, apiPost, apiPut, type ApiState } from "@/lib/api";
import type { OrgPolicy } from "@/lib/org-api";

export interface ModelUnit {
  model: string;
  provider_model?: string;
  input_per_mtok: number;
  output_per_mtok: number;
}

export interface ProviderKey {
  connection_id: string;
  provider: string;
  base_url: string;
  units: ModelUnit[];
  allowed_data_classes: string[];
  key_hint: string;
  enabled: boolean;
  updated_by: string;
  updated_at: string | null;
}

export interface Invite {
  id: string;
  label: string | null;
  created_by: string;
  created_at: string;
  expires_at: string;
  max_uses: number;
  uses: number;
  revoked_at: string | null;
  active: boolean;
}

export interface CreatedInvite {
  id: string;
  code: string;
  expires_at: string;
  max_uses: number;
  label: string | null;
}

export type NextStep = "connect_key" | "set_policy" | "invite_developers" | "waiting_for_developers" | "running";

export interface Onboarding {
  organization_id: string;
  provider_keys: number;
  policy_set: boolean;
  kill_switch: boolean;
  active_invites: number;
  members: number;
  members_ready: number;
  next_step: NextStep;
}

const o = (orgId: string) => `/v1/orgs/${encodeURIComponent(orgId)}`;

export const getOnboarding = (orgId: string, signal?: AbortSignal) => apiGet<Onboarding>(`${o(orgId)}/onboarding`, signal);
export const getProviderKeys = (orgId: string, signal?: AbortSignal) => apiGet<ProviderKey[]>(`${o(orgId)}/provider-keys`, signal);
export const getInvites = (orgId: string, signal?: AbortSignal) => apiGet<Invite[]>(`${o(orgId)}/invites`, signal);

export const putProviderKey = (orgId: string, connectionId: string, body: { provider: string; base_url: string; units: ModelUnit[]; api_key?: string }) =>
  apiPut<ProviderKey>(`${o(orgId)}/provider-keys/${encodeURIComponent(connectionId)}`, body);

export const deleteProviderKey = (orgId: string, connectionId: string) => apiDelete<void>(`${o(orgId)}/provider-keys/${encodeURIComponent(connectionId)}`);

export const applyRecommendedPolicy = (orgId: string, monthly: string, perUserDaily: string) =>
  apiPost<OrgPolicy>(`${o(orgId)}/policy/recommended`, { monthly_budget_usd: monthly.trim(), per_user_daily_budget_usd: perUserDaily.trim() });

export const createInvite = (orgId: string, body: { label?: string; days: number; max_uses: number }) => apiPost<CreatedInvite>(`${o(orgId)}/invites`, body);

export const revokeInvite = (orgId: string, inviteId: string) => apiDelete<void>(`${o(orgId)}/invites/${encodeURIComponent(inviteId)}`);

/**
 * The models box: one model per line, `model, input $/Mtok, output $/Mtok` with an optional fourth column for the
 * provider's own model id when it differs. Prices are required: without one the budget check blocks every call.
 * Returns the units, or the first line that cannot be read.
 */
export function parseUnits(text: string): { ok: true; units: ModelUnit[] } | { ok: false; error: string } {
  const units: ModelUnit[] = [];
  const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);
  if (!lines.length) return { ok: false, error: "List at least one model." };
  for (const [i, line] of lines.entries()) {
    const cols = line.split(",").map((c) => c.trim());
    const [model, inp, out, providerModel] = cols;
    const input = Number(inp);
    const output = Number(out);
    if (cols.length < 3 || cols.length > 4 || !model || /[|\s]/.test(model) || inp === "" || out === "" || !Number.isFinite(input) || !Number.isFinite(output) || input < 0 || output < 0) {
      return { ok: false, error: `Line ${i + 1} ("${line.slice(0, 60)}"): write  model, input price, output price  (USD per million tokens), optionally  , provider model id.` };
    }
    if (units.some((u) => u.model === model)) return { ok: false, error: `Line ${i + 1}: ${model} is listed twice.` };
    units.push({ model, input_per_mtok: input, output_per_mtok: output, ...(providerModel ? { provider_model: providerModel } : {}) });
  }
  return { ok: true, units };
}

/** The whole line a developer runs. Both addresses are the ones this console talks to; the MCP address is a separate service. */
export function joinCommand(code: string, mcpUrl: string, apiUrl: string = API_URL): string {
  const parts = ["npx -y stealthlab-mcp join", code];
  if (mcpUrl) parts.push("--url", mcpUrl);
  if (apiUrl) parts.push("--api", apiUrl);
  return parts.join(" ");
}

export const NEXT_STEP_TEXT: Record<NextStep, string> = {
  connect_key: "Connect your provider key (step 1). Until then there are no models to use.",
  set_policy: "Apply the policy (step 2). With no policy, every call is refused.",
  invite_developers: "Create an invite and send the command to your developers (step 3).",
  waiting_for_developers: "Invites are out. Developers show as ready after their first successful test call.",
  running: "Running. Developers are making calls through your gateway.",
};

export type { ApiState };
