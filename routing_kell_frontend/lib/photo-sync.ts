/**
 * Keeps the profile photo the same on every device. The photo is stored on keळ's server (backend/app/mcp_server/photo_routes.py,
 * the Neon database) under the person's Supabase subject; each browser keeps a copy in localStorage so it shows at once and
 * offline. Calls carry the website-session token as a Bearer to the hosted MCP origin only, no cookies.
 *
 * Every function returns a plain outcome and never throws: the photo is cosmetic, so a server that is down must not break a page.
 * Rule when a browser looks at the server (`pull`): the server wins. If the server has no photo and this browser has one that it
 * never synced, that is a photo from before sync existed, so it is uploaded once. If it was synced before and the server now has
 * none, it was removed on another device, so this browser drops its copy.
 */
import { MCP_ORIGIN } from "@/lib/connector";
import { getAccessToken } from "@/lib/session";

export type Outcome = "ok" | "offline" | "refused";

const syncedKey = (subject: string) => `kel.profile.photo.synced.${subject}`;

export interface PhotoStore {
  read(subject: string): string | null;
  write(subject: string, url: string | null): void;
}

export interface PhotoSyncDeps {
  token(): Promise<string | null>;
  store: PhotoStore;
  /** Remembers that this browser has seen the server's state for the subject. */
  flag: { get(subject: string): boolean; set(subject: string, on: boolean): void };
  origin?: string;
  fetchImpl?: typeof fetch;
}

export interface ImageBytes { type: string; bytes: Uint8Array }

/** The type and bytes inside a jpeg/png/webp data: URL, or null for anything else. */
export function dataUrlToBytes(url: string): ImageBytes | null {
  const m = /^data:(image\/(?:jpeg|png|webp));base64,([A-Za-z0-9+/=]+)$/.exec(url);
  if (!m) return null;
  try {
    const bin = atob(m[2]);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return { type: m[1], bytes };
  } catch { return null; }
}

function bytesToDataUrl(type: string, bytes: Uint8Array): string | null {
  if (!/^image\/(jpeg|png|webp)$/.test(type)) return null;
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return `data:${type};base64,${btoa(bin)}`;
}

export function photoSync(deps: PhotoSyncDeps) {
  const base = `${(deps.origin ?? MCP_ORIGIN).replace(/\/+$/, "")}/v1/me/photo`;
  const doFetch = deps.fetchImpl ?? fetch;

  async function call(method: string, body?: ImageBytes): Promise<Response | null> {
    try {
      const token = await deps.token();
      if (!token) return null;
      return await doFetch(base, {
        method, cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer",
        headers: { authorization: `Bearer ${token}`, ...(body ? { "content-type": body.type } : {}) },
        ...(body ? { body: body.bytes.buffer as ArrayBuffer } : {}),
      });
    } catch { return null; }
  }

  const outcome = (res: Response | null): Outcome => (res === null ? "offline" : res.ok ? "ok" : "refused");

  async function push(subject: string): Promise<Outcome> {
    const url = deps.store.read(subject);
    const img = url ? dataUrlToBytes(url) : null;
    if (!img) return "refused";
    const out = outcome(await call("PUT", img));
    if (out === "ok") deps.flag.set(subject, true);
    return out;
  }

  return {
    /** Sends this browser's photo to the server. */
    push,
    /** Removes the photo from the server. */
    async drop(subject: string): Promise<Outcome> {
      const out = outcome(await call("DELETE"));
      if (out === "ok") deps.flag.set(subject, false);
      return out;
    },
    /** Brings this browser in line with the server (see the rule above). Returns true when the shown photo changed. */
    async pull(subject: string): Promise<boolean> {
      const res = await call("GET");
      if (res === null) return false;
      if (res.status === 404) {
        if (!deps.store.read(subject)) return false;
        if (deps.flag.get(subject)) { deps.store.write(subject, null); deps.flag.set(subject, false); return true; }
        await push(subject);
        return false;
      }
      if (!res.ok) return false;
      let url: string | null;
      try { url = bytesToDataUrl((res.headers.get("content-type") ?? "").split(";")[0], new Uint8Array(await res.arrayBuffer())); } catch { return false; }
      if (!url) return false;
      deps.flag.set(subject, true);
      if (url === deps.store.read(subject)) return false;
      deps.store.write(subject, url);
      return true;
    },
  };
}

export const browserFlag = {
  get(subject: string): boolean { try { return window.localStorage.getItem(syncedKey(subject)) === "1"; } catch { return false; } },
  set(subject: string, on: boolean): void {
    try { if (on) window.localStorage.setItem(syncedKey(subject), "1"); else window.localStorage.removeItem(syncedKey(subject)); } catch { /* none */ }
  },
};

export const defaultToken = () => getAccessToken();
