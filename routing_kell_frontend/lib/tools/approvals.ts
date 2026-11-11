/**
 * The tool-approval API on the hosted server (backend/app/mcp_server/room_agent_routes.py,
 * /v1/tools/approvals; table db/196). An approval is scoped to one exact call shape and is
 * single-use: the gate creates it, the card approves or denies it, and the next identical call
 * consumes it once.
 */
import { MCP_ORIGIN } from "@/lib/connector";

export interface ToolApproval {
  id: string;
  owner_id: string;
  task_id: string | null;
  goal_id: string | null;
  server: string;
  tool: string;
  args_hash: string;
  risk: string;
  data_class: string;
  cert_id: string | null;
  status: "pending" | "approved" | "denied" | "expired" | "used";
  requested_at: string;
  decided_at: string | null;
  expires_at: string;
  used_at: string | null;
  decided_by: string | null;
  note: string | null;
}

export class ApprovalsApiError extends Error {
  constructor(message: string, readonly status?: number) { super(message); this.name = "ApprovalsApiError"; }
  get offline() { return this.status === undefined; }
}

export interface ApprovalsDeps { token(): Promise<string | null>; origin?: string; fetchImpl?: typeof fetch }

export function approvalsApi(deps: ApprovalsDeps) {
  const base = (deps.origin ?? MCP_ORIGIN).replace(/\/+$/, "");
  const doFetch = deps.fetchImpl ?? fetch;

  async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
    const token = await deps.token();
    if (!token) throw new ApprovalsApiError("Sign in again to review tool approvals.", 401);
    let res: Response;
    try {
      res = await doFetch(`${base}/v1/tools/approvals${path}`, {
        method, cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer",
        headers: { authorization: `Bearer ${token}`, ...(body !== undefined ? { "content-type": "application/json" } : {}) },
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      });
    } catch {
      throw new ApprovalsApiError("Couldn't reach the kell server.");
    }
    let json: unknown = null;
    try { json = await res.json(); } catch { /* no body */ }
    if (!res.ok) {
      const msg = (json as { error?: unknown } | null)?.error;
      throw new ApprovalsApiError(typeof msg === "string" && msg ? msg : `The server answered ${res.status}.`, res.status);
    }
    return json as T;
  }

  const e = encodeURIComponent;
  return {
    list: async () => (await call<{ approvals: ToolApproval[] }>("GET", "")).approvals,
    get: async (id: string) => (await call<{ approval: ToolApproval }>("GET", `/${e(id)}`)).approval,
    approve: async (id: string) => (await call<{ approval: ToolApproval }>("POST", `/${e(id)}/approve`)).approval,
    deny: async (id: string) => (await call<{ approval: ToolApproval }>("POST", `/${e(id)}/deny`)).approval,
  };
}
