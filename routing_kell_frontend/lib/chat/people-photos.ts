/**
 * Other people's profile photos in a room. The server shows a person's photo only to someone who shares a room with them
 * (backend/app/mcp_server/photo_routes.py, GET /v1/people/{sub}/photo), and it needs the session token, so a plain <img src> cannot
 * fetch it: the page fetches it with the token and shows it from a local object URL.
 *
 * One request per person per page load (shared by every message from them), and a person with no photo is not asked again for
 * five minutes. Failures return null, so the avatar falls back to initials; nothing here can break a room.
 */
import { MCP_ORIGIN } from "@/lib/connector";
import { getAccessToken } from "@/lib/session";

const RETRY_MS = 5 * 60 * 1000;
type Entry = { url: string | null; at: number; pending?: Promise<string | null> };
const cache = new Map<string, Entry>();

export interface PhotoDeps { token(): Promise<string | null>; fetchImpl?: typeof fetch; origin?: string; now?(): number; makeUrl?(b: Blob): string }

export async function personPhoto(sub: string, d: PhotoDeps = { token: getAccessToken }): Promise<string | null> {
  const now = (d.now ?? Date.now)();
  const hit = cache.get(sub);
  if (hit?.pending) return hit.pending;
  if (hit && (hit.url || now - hit.at < RETRY_MS)) return hit.url;
  const pending = (async (): Promise<string | null> => {
    try {
      const token = await d.token();
      if (!token) return null;
      const res = await (d.fetchImpl ?? ((...a) => fetch(...a)))(`${(d.origin ?? MCP_ORIGIN).replace(/\/+$/, "")}/v1/people/${encodeURIComponent(sub)}/photo`, {
        cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer", headers: { authorization: `Bearer ${token}` },
      });
      if (!res.ok) return null;
      return (d.makeUrl ?? ((b: Blob) => URL.createObjectURL(b)))(await res.blob());
    } catch { return null; }
  })();
  cache.set(sub, { url: null, at: now, pending });
  const url = await pending;
  cache.set(sub, { url, at: now });
  return url;
}

export function forgetPeoplePhotos(): void { cache.clear(); }
