/**
 * The rooms API on the hosted server (backend/app/mcp_server/room_routes.py; tables in db/157_rooms.sql): rooms and chats that
 * follow you across devices. Calls carry your website-session token as a Bearer, to the hosted MCP origin only, no cookies.
 * Rooms are OPEN rooms: keळ's server can read them (that is what lets connectors such as Claude and ChatGPT join), so they are
 * not end-to-end encrypted, and the room says so.
 */
import { MCP_ORIGIN } from "@/lib/connector";

export interface ServerRoom {
  id: string; title: string; mode: "open" | "private"; role: "owner" | "admin" | "member"; created_at: string; last_seq: number;
  last_read_seq?: number; unread?: number; members?: number;
  last?: { handle: string; kind: "person" | "agent"; preview: string; at: string } | null;
}
export type EventKind = "msg" | "edit" | "delete" | "react" | "system" | "task" | "task_update" | "note";
export interface ServerEvent {
  id: string; room_id: string; seq: number; kind: EventKind;
  sender: { kind: "person" | "agent"; handle: string; sub: string; name: string | null; me: boolean };
  body: string; target: string | null; reply_to: string | null; lamport: number; mentions: string[]; depth: number; device_id: string | null; created_at: string;
}
export interface EventIn {
  id: string; kind: EventKind; body: string; sender_kind: "person" | "agent"; sender_handle?: string; lamport?: number; depth?: number;
  device_id?: string; target?: string; reply_to?: string;
}
export interface AppendResult { accepted: Array<{ id: string; seq: number; duplicate?: boolean }>; rejected: Array<{ id: string | null; reason: string; retry: boolean }> }
export interface EventsPage { events: ServerEvent[]; next: number; more: boolean; last_seq: number }
export interface RoomMember { kind: "person" | "agent"; sub: string | null; agent_id: string | null; name: string | null; role: "owner" | "admin" | "member"; added_at: string; me: boolean }
export interface RoomInvite { id: string; code: string; expires_at: string; max_uses: number }

export class RoomsApiError extends Error {
  constructor(message: string, readonly status?: number) { super(message); this.name = "RoomsApiError"; }
  /** The server could not be reached (as opposed to it answering with a refusal). */
  get offline() { return this.status === undefined; }
}

export interface RoomsDeps { token(): Promise<string | null>; origin?: string; fetchImpl?: typeof fetch }

export function roomsApi(deps: RoomsDeps) {
  const base = (deps.origin ?? MCP_ORIGIN).replace(/\/+$/, "");
  const doFetch = deps.fetchImpl ?? fetch;

  async function call<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
    const token = await deps.token();
    if (!token) throw new RoomsApiError("Sign in again to sync your rooms.", 401);
    let res: Response;
    try {
      res = await doFetch(`${base}/v1/rooms${path}`, {
        method, signal, cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer",
        headers: { authorization: `Bearer ${token}`, ...(body !== undefined ? { "content-type": "application/json" } : {}) },
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      });
    } catch (err) {
      if ((err as { name?: string })?.name === "AbortError") throw err;
      throw new RoomsApiError("Couldn't reach the keळ server.");
    }
    let json: unknown = null;
    try { json = await res.json(); } catch { /* no body */ }
    if (!res.ok) {
      const msg = (json as { error?: unknown } | null)?.error;
      throw new RoomsApiError(typeof msg === "string" && msg ? msg : `The server answered ${res.status}.`, res.status);
    }
    return json as T;
  }
  const e = encodeURIComponent;

  return {
    rooms: async () => (await call<{ rooms: ServerRoom[] }>("GET", "")).rooms,
    createRoom: (title: string, id?: string) => call<ServerRoom>("POST", "", { title, ...(id ? { id } : {}) }),
    renameRoom: (id: string, title: string) => call<ServerRoom>("PATCH", `/${e(id)}`, { title }),
    deleteRoom: (id: string) => call<{ deleted: string; events_removed: number }>("DELETE", `/${e(id)}`),
    members: async (id: string) => (await call<{ members: RoomMember[] }>("GET", `/${e(id)}/members`)).members,
    setMemberRole: (id: string, sub: string, role: "admin" | "member") => call<{ sub: string; role: string }>("PATCH", `/${e(id)}/members/${e(sub)}`, { role }),
    removeMember: (id: string, sub: string) => call<{ removed: string }>("DELETE", `/${e(id)}/members/${e(sub)}`),
    createInvite: (id: string, days = 7, maxUses = 20) => call<RoomInvite>("POST", `/${e(id)}/invites`, { days, max_uses: maxUses }),
    revokeInvite: (id: string, inviteId: string) => call<{ revoked: string }>("DELETE", `/${e(id)}/invites/${e(inviteId)}`),
    join: (code: string) => call<{ room_id: string; title: string; already_member: boolean }>("POST", "/join", { code }),
    events: (id: string, after = 0, limit = 200, signal?: AbortSignal) => call<EventsPage>("GET", `/${e(id)}/events?after=${after}&limit=${limit}`, undefined, signal),
    append: (id: string, events: EventIn[]) => call<AppendResult>("POST", `/${e(id)}/events`, { events }),
    markRead: (id: string, seq: number) => call<{ last_read_seq: number }>("PUT", `/${e(id)}/read`, { seq }),
  };
}
export type RoomsApi = ReturnType<typeof roomsApi>;
