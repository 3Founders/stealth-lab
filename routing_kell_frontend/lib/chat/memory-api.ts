/**
 * kell's memory on the hosted server (backend/app/mcp_server/memory_routes.py, services/memory.py): a short index kell always knows, topic
 * files, your notes and knowledge files, with every change kept as a version. It is OFF until you turn it on, it lives on keळ's server
 * (not end-to-end encrypted, like rooms), and you can see, edit, undo and delete all of it. Calls carry your website-session token as a
 * Bearer, to the hosted MCP origin only.
 */
import { MCP_ORIGIN } from "@/lib/connector";

export interface MemorySettings { enabled: boolean; share_agents: boolean; last_dream_at: string | null }
export interface MemoryFileInfo { path: string; version: number; size: number; updated_at: string }
export interface MemoryFile { path: string; body: string; version: number; updated_at?: string }
export interface MemoryDream { id: string; at: string; changed: string[]; note: string }
export interface MemoryOverview { settings: MemorySettings; files: MemoryFileInfo[]; dreams: MemoryDream[] }

export class MemoryApiError extends Error {
  constructor(message: string, readonly status?: number) { super(message); this.name = "MemoryApiError"; }
}

export interface MemoryDeps { token(): Promise<string | null>; origin?: string; fetchImpl?: typeof fetch }

/** A note's file name from its title: notes/<title>.md with only safe characters. */
export const notePath = (title: string): string => `notes/${(title || "").replace(/[^A-Za-z0-9._ -]+/g, "").trim().slice(0, 60).replace(/^[ .-]+|[ .-]+$/g, "") || "untitled"}.md`;
export const knowledgePath = (name: string): string => `knowledge/${(name || "").replace(/\.[^.]*$/, "").replace(/[^A-Za-z0-9._ -]+/g, "").trim().slice(0, 60).replace(/^[ .-]+|[ .-]+$/g, "") || "file"}.md`;
export const titleOf = (path: string): string => path.replace(/^[^/]+\//, "").replace(/\.md$/, "");

export function memoryApi(deps: MemoryDeps) {
  const base = (deps.origin ?? MCP_ORIGIN).replace(/\/+$/, "");
  const doFetch = deps.fetchImpl ?? fetch;
  async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
    const token = await deps.token();
    if (!token) throw new MemoryApiError("Sign in again to use memory.", 401);
    let res: Response;
    try {
      res = await doFetch(`${base}/v1/memory${path}`, {
        method, cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer",
        headers: { authorization: `Bearer ${token}`, ...(body !== undefined ? { "content-type": "application/json" } : {}) },
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      });
    } catch { throw new MemoryApiError("Couldn't reach the keळ server."); }
    let json: unknown = null;
    try { json = await res.json(); } catch { /* no body */ }
    if (!res.ok) {
      const msg = (json as { error?: unknown } | null)?.error;
      throw new MemoryApiError(typeof msg === "string" && msg ? msg : `The server answered ${res.status}.`, res.status);
    }
    return json as T;
  }
  const q = encodeURIComponent;
  return {
    overview: () => call<MemoryOverview>("GET", ""),
    setSettings: (s: Partial<Pick<MemorySettings, "enabled" | "share_agents">>) => call<MemorySettings>("PUT", "/settings", s),
    read: (path: string) => call<MemoryFile>("GET", `/file?path=${q(path)}`),
    write: (path: string, body: string, expectedVersion?: number, reason?: string) =>
      call<{ path: string; version: number; unchanged?: boolean }>("PUT", "/file", { path, body, ...(expectedVersion !== undefined ? { expected_version: expectedVersion } : {}), ...(reason ? { reason } : {}) }),
    remove: (path: string) => call<{ deleted: boolean }>("DELETE", `/file?path=${q(path)}`),
    versions: (path: string) => call<{ versions: Array<{ version: number; by: string; reason: string; at: string; size: number }> }>("GET", `/versions?path=${q(path)}`),
    revert: (path: string, version: number) => call<{ path: string; version: number }>("POST", "/revert", { path, version }),
    deleteAll: () => call<{ deleted: boolean; settings: MemorySettings }>("DELETE", ""),
    /** Asks for a dream (a tidy-up). Without `force` the server only runs one when it is due. */
    dream: (force = false) => call<{ ran: boolean; why?: string; changed?: string[]; dreams?: MemoryDream[] }>("POST", "/dream", { force }),
  };
}
export type MemoryApi = ReturnType<typeof memoryApi>;
