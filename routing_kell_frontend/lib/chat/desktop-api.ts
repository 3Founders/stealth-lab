/**
 * Desktop sessions on the hosted server (backend/app/mcp_server/desktop_routes.py, services/desktop_remote.py): the kell desktop app
 * reports its sessions and runs what is asked of them from here. Your own sessions you can drive like a remote control; a teammate's,
 * when they gave you access. Calls carry your website-session token as a Bearer, to the hosted MCP origin only.
 */
import { MCP_ORIGIN } from "@/lib/connector";

export type GrantLevel = "answer" | "approve" | "full";
export interface DesktopSession {
  device_id: string; session_id: string; owner: string; title: string; project: string; summary: string;
  updated_at: string | null; device: string; online: boolean;
}
export interface DesktopDevice { id: string; name: string; last_seen: string | null; online: boolean }
export interface DesktopAsk {
  id: string; owner: string; asker: string; device_id: string; session_id: string; room_id: string | null; text: string;
  level: "owner" | GrantLevel; status: "needs_approval" | "queued" | "running" | "waiting_permission" | "done" | "failed" | "denied" | "cancelled";
  answer: string; waiting: { request_id: string; tool: string; detail: string } | null; decision: string | null; error: string;
  created_at: string | null; updated_at: string | null; asker_name?: string;
}
export interface DesktopOverview { mine: DesktopSession[]; devices: DesktopDevice[]; shared: DesktopSession[]; pending: DesktopAsk[] }
export interface Grantee { sub: string; username: string; name: string | null; level: GrantLevel; since: string | null }
export interface Grants { given: Grantee[]; received: Grantee[] }

export const LEVEL_LABEL: Record<GrantLevel, string> = {
  answer: "Answer only (reads, never edits)",
  approve: "You approve each ask",
  full: "Full access",
};
/** An ask that is still going: the panel keeps polling it. */
export const isActive = (a: DesktopAsk) => ["needs_approval", "queued", "running", "waiting_permission"].includes(a.status);

export class DesktopApiError extends Error {
  constructor(message: string, readonly status?: number) { super(message); this.name = "DesktopApiError"; }
}

export interface DesktopDeps { token(): Promise<string | null>; origin?: string; fetchImpl?: typeof fetch }

export function desktopApi(deps: DesktopDeps) {
  const base = (deps.origin ?? MCP_ORIGIN).replace(/\/+$/, "");
  const doFetch = deps.fetchImpl ?? fetch;
  async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
    const token = await deps.token();
    if (!token) throw new DesktopApiError("Sign in again to reach your desktop.", 401);
    let res: Response;
    try {
      res = await doFetch(`${base}/v1/desktop${path}`, {
        method, cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer",
        headers: { authorization: `Bearer ${token}`, ...(body !== undefined ? { "content-type": "application/json" } : {}) },
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      });
    } catch { throw new DesktopApiError("Couldn't reach the keळ server."); }
    let json: unknown = null;
    try { json = await res.json(); } catch { /* no body */ }
    if (!res.ok) {
      const msg = (json as { error?: unknown } | null)?.error;
      throw new DesktopApiError(typeof msg === "string" && msg ? msg : `The server answered ${res.status}.`, res.status);
    }
    return json as T;
  }
  return {
    overview: () => call<DesktopOverview>("GET", "/sessions"),
    asks: (sessionId?: string) => call<{ asks: DesktopAsk[] }>("GET", `/asks${sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ""}`).then((r) => r.asks),
    ask: (s: { device_id: string; session_id: string }, text: string) => call<DesktopAsk>("POST", "/asks", { device_id: s.device_id, session_id: s.session_id, text }),
    get: (id: string) => call<DesktopAsk>("GET", `/asks/${encodeURIComponent(id)}`),
    /** Your own session's recent conversation: the desktop answers this request with the lines as JSON (poll `get`). */
    history: (s: { device_id: string; session_id: string }) => call<DesktopAsk>("POST", "/history", { device_id: s.device_id, session_id: s.session_id }),
    approve: (id: string, yes: boolean) => call<DesktopAsk>("POST", `/asks/${encodeURIComponent(id)}/approve`, { yes }),
    permission: (id: string, decision: "once" | "always" | "reject") => call<DesktopAsk>("POST", `/asks/${encodeURIComponent(id)}/permission`, { decision }),
    cancel: (id: string) => call<DesktopAsk>("POST", `/asks/${encodeURIComponent(id)}/cancel`),
    grants: () => call<Grants>("GET", "/grants"),
    grant: (username: string, level: GrantLevel) => call<{ ok: true }>("POST", "/grants", { username, level }),
    revoke: (grantee: string) => call<{ ok: true }>("DELETE", `/grants?grantee=${encodeURIComponent(grantee)}`),
  };
}
export type DesktopApi = ReturnType<typeof desktopApi>;
