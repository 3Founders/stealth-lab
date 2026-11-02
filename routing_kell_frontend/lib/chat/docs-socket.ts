/**
 * The live connection to one shared document (backend/app/mcp_server/room_doc_ws.py): a WebSocket on which you send your edits and
 * receive everyone's saved versions and who is here. It reconnects by itself (growing waits, up to 15 s) and the panel keeps working over
 * the plain HTTP routes while it is down. The token is sent as the first message, never in the address.
 */
import { MCP_ORIGIN } from "@/lib/connector";
import type { DocKind } from "@/lib/chat/docs-api";

export type SocketMessage =
  | { type: "hello"; id: string; version: number; title: string; kind: DocKind; body: string; present: string[]; editing: string[] }
  | { type: "ack"; version: number; body?: string; merged?: boolean; clash?: boolean; unchanged?: boolean }
  | { type: "doc"; version: number; body: string; by: string }
  | { type: "presence"; present: string[]; editing: string[] }
  | { type: "cursors"; cursors: Array<{ name: string; from: number; to: number }> }
  | { type: "error"; error: string; status?: number }
  | { type: "pong" };

export interface DocSocketHandlers {
  onMessage(m: SocketMessage): void;
  /** True once the socket is open and authenticated, false whenever it drops. */
  onState(connected: boolean): void;
}

export interface DocSocket { send(m: { type: "edit"; body: string; base: number } | { type: "cursor"; from: number; to: number } | { type: "ping" }): boolean; close(): void; readonly connected: boolean }

export const socketsAvailable = (): boolean => typeof WebSocket !== "undefined";

export function openDocSocket(opts: { room: string; doc: string; token(): Promise<string | null>; origin?: string; socketFactory?: (url: string) => WebSocket }, h: DocSocketHandlers): DocSocket {
  const base = (opts.origin ?? MCP_ORIGIN).replace(/\/+$/, "").replace(/^http/, "ws");
  const url = `${base}/v1/rooms/${encodeURIComponent(opts.room)}/docs/${encodeURIComponent(opts.doc)}/ws`;
  let ws: WebSocket | null = null;
  let closed = false;
  let up = false;
  let tries = 0;
  let beat: ReturnType<typeof setInterval> | undefined;
  let retry: ReturnType<typeof setTimeout> | undefined;

  const set = (v: boolean) => { if (up !== v) { up = v; h.onState(v); } };
  const connect = async () => {
    if (closed) return;
    const token = await opts.token();
    if (!token || closed) { retry = setTimeout(() => void connect(), 5000); return; }
    let s: WebSocket;
    try { s = opts.socketFactory ? opts.socketFactory(url) : new WebSocket(url); } catch { retry = setTimeout(() => void connect(), 15000); return; }
    ws = s;
    s.onopen = () => { s.send(JSON.stringify({ type: "auth", token })); };
    s.onmessage = (ev) => {
      let m: SocketMessage;
      try { m = JSON.parse(String(ev.data)) as SocketMessage; } catch { return; }
      if (m.type === "hello") { tries = 0; set(true); }
      if (m.type === "error" && (m.status === 401 || m.status === 404)) { closed = true; set(false); s.close(); return; }  // not allowed: stay on the HTTP routes
      h.onMessage(m);
    };
    s.onclose = () => {
      set(false);
      clearInterval(beat);
      if (closed) return;
      tries += 1;
      retry = setTimeout(() => void connect(), Math.min(15000, 500 * 2 ** Math.min(tries, 5)));
    };
    s.onerror = () => { try { s.close(); } catch { /* already closing */ } };
    clearInterval(beat);
    beat = setInterval(() => { if (s.readyState === 1) s.send(JSON.stringify({ type: "ping" })); }, 20000);
  };
  void connect();

  return {
    get connected() { return up; },
    send(m) { if (!ws || ws.readyState !== 1 || !up) return false; ws.send(JSON.stringify(m)); return true; },
    close() { closed = true; clearTimeout(retry); clearInterval(beat); set(false); try { ws?.close(); } catch { /* already closed */ } },
  };
}
