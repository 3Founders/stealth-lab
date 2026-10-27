// The page's only way to reach the local helper. It carries the per-run token, and it never carries a key anywhere but
// the body of the one PUT that saves it.
import { readSse } from "@/lib/sse";
import { HelperError, helperMessage, isLoopbackAddress, scrub, type HelperFailure } from "@/lib/words";
import type { ChatEvent, ChatMessage, ClientId, ClientInfo, InstallResult, KeyInfo, ModelInfo, NewModel, ReadinessItem, TestResult } from "@/lib/types";

const TOKEN_STORE = "kel.console.token";

export interface Connection {
  token: string | null;
  /** "" means the page's own origin (the helper served this page). Otherwise a loopback address. */
  base: string;
  /** The query string to leave in the address bar once the token has been taken out. */
  cleanSearch: string;
}

/**
 * Reads `?t=<token>` once and removes it from the address, so the token does not sit in history or in a Referer header.
 * `?api=<loopback address>` points the page at a helper on another port (development); anything not loopback is ignored,
 * so a crafted link cannot send the token to another host.
 */
export function readConnection(search: string, stored: string | null): Connection {
  const q = new URLSearchParams(search);
  const fromUrl = q.get("t");
  const api = q.get("api");
  q.delete("t");
  const base = api && isLoopbackAddress(api) ? api.replace(/\/+$/, "") : "";
  if (api && !base) q.delete("api");
  const rest = q.toString();
  return { token: fromUrl || stored || null, base, cleanSearch: rest ? `?${rest}` : "" };
}

/** Browser wiring for readConnection; storage holds the token only (never a key), for this tab only. */
export function connectFromLocation(): Connection {
  let stored: string | null = null;
  try { stored = window.sessionStorage.getItem(TOKEN_STORE); } catch { /* storage may be blocked */ }
  const c = readConnection(window.location.search, stored);
  if (c.token) {
    try { window.sessionStorage.setItem(TOKEN_STORE, c.token); } catch { /* fine without it */ }
  }
  if (window.location.search !== c.cleanSearch) {
    window.history.replaceState(null, "", `${window.location.pathname}${c.cleanSearch}${window.location.hash}`);
  }
  return c;
}

type FetchLike = typeof fetch;

export class HelperClient {
  constructor(private conn: Connection, private fetchImpl: FetchLike = (...a) => fetch(...a)) {}

  private async call<T>(method: string, path: string, body?: unknown, signal?: AbortSignal, secrets: string[] = []): Promise<T> {
    let res: Response;
    try {
      res = await this.fetchImpl(`${this.conn.base}${path}`, {
        method,
        signal,
        cache: "no-store",
        headers: { ...(this.conn.token ? { "x-console-token": this.conn.token } : {}), ...(body !== undefined ? { "content-type": "application/json" } : {}) },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (err) {
      if ((err as { name?: string })?.name === "AbortError") throw err;
      throw new HelperError("unreachable", helperMessage("unreachable", null));
    }
    if (!res.ok) throw await this.failure(res, secrets);
    return (await res.json()) as T;
  }

  private async failure(res: Response, secrets: string[]): Promise<HelperError> {
    const kind: HelperFailure = res.status === 401 ? "unauthorized" : res.status === 403 ? "forbidden" : res.status >= 500 ? "failed" : "refused";
    let body: unknown = null;
    try { body = await res.json(); } catch { /* not JSON: fixed wording is used */ }
    return new HelperError(kind, scrub(helperMessage(kind, body), ...secrets), res.status);
  }

  /** Needs no token: tells "helper is down" from "token is wrong". */
  async health(): Promise<boolean> {
    try {
      const r = await this.fetchImpl(`${this.conn.base}/health`, { cache: "no-store" });
      return r.ok;
    } catch {
      return false;
    }
  }

  clients = () => this.call<ClientInfo[]>("GET", "/clients");
  install = (clients: ClientId[], opts: { cursor_model?: string; dry_run?: boolean } = {}) =>
    this.call<InstallResult[]>("POST", "/install", { clients, ...opts });
  uninstall = (clients: ClientId[]) => this.call<InstallResult[]>("POST", "/uninstall", { clients });
  readiness = () => this.call<ReadinessItem[]>("GET", "/readiness");
  models = () => this.call<ModelInfo[]>("GET", "/models");
  addModel = (m: NewModel) => this.call<{ ok: boolean; name: string }>("POST", "/models", m);
  removeModel = (name: string) => this.call<{ ok: boolean }>("DELETE", `/models/${encodeURIComponent(name)}`);
  testModel = (name: string) => this.call<TestResult>("POST", `/models/${encodeURIComponent(name)}/test`, {});
  keys = () => this.call<KeyInfo[]>("GET", "/keys");
  /** The one place a key leaves the page: the body of this request. It is not kept anywhere on the way. */
  saveKey = (envName: string, value: string) =>
    this.call<{ ok: boolean; last4: string | null }>("PUT", `/keys/${encodeURIComponent(envName)}`, { value }, undefined, [value]);
  removeKey = (envName: string) => this.call<{ ok: boolean; removed?: boolean }>("DELETE", `/keys/${encodeURIComponent(envName)}`);

  /** Streams /chat; onEvent runs as each event arrives. Aborting `signal` closes the request, which stops every model. */
  async chat(models: string[], messages: ChatMessage[], onEvent: (e: ChatEvent) => void, signal: AbortSignal): Promise<void> {
    let res: Response;
    try {
      res = await this.fetchImpl(`${this.conn.base}/chat`, {
        method: "POST",
        signal,
        cache: "no-store",
        headers: { "content-type": "application/json", ...(this.conn.token ? { "x-console-token": this.conn.token } : {}) },
        body: JSON.stringify({ models, messages }),
      });
    } catch (err) {
      if ((err as { name?: string })?.name === "AbortError") return;
      throw new HelperError("unreachable", helperMessage("unreachable", null));
    }
    if (!res.ok || !res.body) throw await this.failure(res, []);
    try {
      await readSse(res.body, (e) => {
        if (e && typeof e === "object" && typeof (e as { model?: unknown }).model === "string") onEvent(e as ChatEvent);
      });
    } catch (err) {
      if ((err as { name?: string })?.name === "AbortError") return;
      throw new HelperError("unreachable", helperMessage("unreachable", null));
    }
  }
}
