/**
 * The shared documents of a room (backend/app/mcp_server/room_doc_routes.py, services/room_docs.py): the canvas everyone in the room sees
 * and edits at once. Calls carry your website-session token as a Bearer, to the hosted MCP origin only.
 */
import { MCP_ORIGIN } from "@/lib/connector";

export type DocKind = "document" | "code" | "html";
export interface DocInfo { id: string; title: string; kind: DocKind; version: number; updated_by: string; updated_at: string | null; size: number | null }
export interface DocFull { id: string; version: number; present: string[]; title: string; kind: DocKind; body: string; updated_by: string; updated_at: string | null }
export interface DocUnchanged { id: string; version: number; present: string[]; unchanged: true }
export interface SaveResult { version: number; unchanged?: boolean; merged?: boolean; clash?: boolean; body?: string }
export interface DocVersion { version: number; by: string; note: string; size: number; at: string | null }

export class DocsApiError extends Error {
  constructor(message: string, readonly status?: number) { super(message); this.name = "DocsApiError"; }
  get offline() { return this.status === undefined; }
}

export interface DocsDeps { token(): Promise<string | null>; origin?: string; fetchImpl?: typeof fetch }

export function docsApi(deps: DocsDeps) {
  const base = (deps.origin ?? MCP_ORIGIN).replace(/\/+$/, "");
  const doFetch = deps.fetchImpl ?? fetch;
  const e = encodeURIComponent;
  async function call<T>(method: string, room: string, path: string, body?: unknown): Promise<T> {
    const token = await deps.token();
    if (!token) throw new DocsApiError("Sign in again to use documents.", 401);
    let res: Response;
    try {
      res = await doFetch(`${base}/v1/rooms/${e(room)}/docs${path}`, {
        method, cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer",
        headers: { authorization: `Bearer ${token}`, ...(body !== undefined ? { "content-type": "application/json" } : {}) },
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      });
    } catch { throw new DocsApiError("Couldn't reach the keळ server."); }
    let json: unknown = null;
    try { json = await res.json(); } catch { /* no body */ }
    if (!res.ok) {
      const msg = (json as { error?: unknown } | null)?.error;
      throw new DocsApiError(typeof msg === "string" && msg ? msg : `The server answered ${res.status}.`, res.status);
    }
    return json as T;
  }
  return {
    list: async (room: string) => (await call<{ docs: DocInfo[] }>("GET", room, "")).docs,
    create: (room: string, title: string, kind: DocKind = "document", body = "") => call<{ id: string; title: string; kind: DocKind; version: number }>("POST", room, "", { title, kind, body }),
    /** The document, or `{unchanged: true}` when nothing was saved since `since`. Asking also tells the others you are here. */
    get: (room: string, id: string, since?: number) => call<DocFull | DocUnchanged>("GET", room, `/${e(id)}${since !== undefined ? `?since=${since}` : ""}`),
    save: (room: string, id: string, body: string, baseVersion: number) => call<SaveResult>("PUT", room, `/${e(id)}`, { body, base_version: baseVersion }),
    remove: (room: string, id: string) => call<{ deleted: boolean }>("DELETE", room, `/${e(id)}`),
    history: async (room: string, id: string) => (await call<{ versions: DocVersion[] }>("GET", room, `/${e(id)}/history`)).versions,
    restore: (room: string, id: string, version: number) => call<{ version: number; body: string }>("POST", room, `/${e(id)}/restore`, { version }),
  };
}
export type DocsApi = ReturnType<typeof docsApi>;
