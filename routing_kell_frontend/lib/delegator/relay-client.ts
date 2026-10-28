/**
 * The real relay (docs/delegate_relay_api.md): agents and tasks live on keळ's hosted server, tied to your account, so they keep
 * working when this page is closed. An agent you add here is your own agent (in Claude Code, Cursor and the like) that reads its
 * inbox with the `delegate_inbox` tool. Calls carry your website-session token as a Bearer, to the hosted MCP origin only.
 * Rooms are labels on tasks; there is no room object on the server.
 */
import { MCP_ORIGIN } from "@/lib/connector";

export interface RelayAgent { id: string; name: string; kind: string; handle: string; created_at: string; last_seen: string | null; online: boolean; revoked: boolean }
export type TaskStatus = "needs_approval" | "queued" | "claimed" | "done" | "failed" | "cancelled";
export interface RelayTask {
  id: string; agent_id: string; room: string; from: string; text: string; locators: unknown[]; depth: number; status: TaskStatus;
  created_at: string; claimed_at: string | null; finished_at: string | null; result: string | null; check: string | null;
}

export class RelayError extends Error {
  constructor(message: string, readonly status?: number) { super(message); this.name = "RelayError"; }
}

export interface RelayDeps { token(): Promise<string | null>; origin?: string; fetchImpl?: typeof fetch }

const AGENT_NAME = /^[a-z0-9][a-z0-9_.-]{0,39}$/;
export function validAgentName(raw: string): { name: string } | { error: string } {
  const name = raw.trim().replace(/^@/, "").toLowerCase();
  if (!AGENT_NAME.test(name)) return { error: "Use up to 40 letters, numbers, dots, dashes or underscores." };
  if (/claude|codex|gpt/.test(name)) return { error: "Choose a name that is yours (it can't contain Claude, Codex or GPT)." };
  return { name };
}

/** The agent a mention names: "@web-chat" and "@alice/web-chat" are both "web-chat". */
export const mentionedNames = (text: string): string[] =>
  [...new Set([...text.matchAll(/(?:^|\s)@(?:[a-z0-9_.-]+\/)?([a-z0-9][a-z0-9_.-]{0,39})/gi)].map((m) => m[1].toLowerCase()))];

export function relay(deps: RelayDeps) {
  const base = (deps.origin ?? MCP_ORIGIN).replace(/\/+$/, "");
  const doFetch = deps.fetchImpl ?? fetch;

  async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
    const token = await deps.token();
    if (!token) throw new RelayError("Sign in again to use agents.", 401);
    let res: Response;
    try {
      res = await doFetch(`${base}/v1/delegate${path}`, {
        method, cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer",
        headers: { authorization: `Bearer ${token}`, ...(body !== undefined ? { "content-type": "application/json" } : {}) },
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      });
    } catch { throw new RelayError("Couldn't reach the keळ server. Check your connection and try again."); }
    let json: unknown = null;
    try { json = await res.json(); } catch { /* no body */ }
    if (!res.ok) {
      const msg = (json as { error?: unknown } | null)?.error;
      throw new RelayError(typeof msg === "string" && msg ? msg : `The server answered ${res.status}.`, res.status);
    }
    return json as T;
  }

  const q = (o: Record<string, string | undefined>) => {
    const s = new URLSearchParams(Object.entries(o).filter(([, v]) => v) as [string, string][]).toString();
    return s ? `?${s}` : "";
  };

  return {
    agents: async () => (await call<{ agents: RelayAgent[] }>("GET", "/agents")).agents,
    addAgent: (name: string) => call<RelayAgent>("POST", "/agents", { name }),
    removeAgent: (id: string) => call<{ revoked: boolean; cancelled: number }>("DELETE", `/agents/${encodeURIComponent(id)}`),
    send: (text: string, room: string, needsApproval = false) =>
      call<{ room: string; tasks: RelayTask[]; unknown: string[] }>("POST", "/messages", { text, room, ...(needsApproval ? { needs_approval: true } : {}) }),
    tasks: async (room: string, after?: string) => (await call<{ tasks: RelayTask[] }>("GET", `/tasks${q({ room, after })}`)).tasks,
    approve: (id: string) => call<RelayTask>("POST", `/tasks/${encodeURIComponent(id)}/approve`),
    cancel: (id: string) => call<RelayTask>("POST", `/tasks/${encodeURIComponent(id)}/cancel`),
  };
}

export const isFinished = (t: RelayTask) => t.status === "done" || t.status === "failed" || t.status === "cancelled";

/** Replaces tasks by id (a replay never duplicates) and keeps them oldest first. */
export function mergeTasks(have: RelayTask[], incoming: RelayTask[]): RelayTask[] {
  const by = new Map(have.map((t) => [t.id, t]));
  for (const t of incoming) by.set(t.id, t);
  return [...by.values()].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id));
}

/** The `after` cursor for the next poll: the latest moment any task changed, a second back so nothing is missed. */
export function cursorOf(tasks: RelayTask[]): string | undefined {
  let latest = 0;
  for (const t of tasks) for (const s of [t.created_at, t.claimed_at, t.finished_at]) { const n = s ? Date.parse(s) : NaN; if (n > latest) latest = n; }
  return latest ? new Date(latest - 1000).toISOString() : undefined;
}
