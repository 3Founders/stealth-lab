/**
 * NOT FOR USE. Retired by docs/rooms_design.md (rev 2): hand-written group key handling must not ship without review. Private rooms
 * will use MLS (RFC 9420) through ts-mls instead. This file stays, tested but unused, until it is deleted or replaced; do not import it
 * from product code.
 */
/**
 * Invite links for rooms. The link is `/room/join?id=<inviteId>#<fragment>`; the fragment carries the room key for one epoch and
 * is never sent to a server (browsers don't send the part after #). Whoever holds the link can read the room from that epoch on,
 * so it is a bearer secret: revoke it (which starts a new epoch) if it leaks. Link previews and browser history can leak it.
 */
import { fromB64Url, toB64Url } from "./crypto";

export interface InviteFragment { epoch: number; key: CryptoKey }

export async function inviteFragment(roomKey: CryptoKey, epoch: number): Promise<string> {
  const raw = new Uint8Array(await crypto.subtle.exportKey("raw", roomKey));
  return `k1.${epoch}.${toB64Url(raw)}`;
}

/** The key and epoch from a link's fragment (with or without the leading #), or null if it isn't one. */
export async function parseInviteFragment(fragment: string): Promise<InviteFragment | null> {
  const m = /^#?k1\.(\d{1,6})\.([A-Za-z0-9_-]{43})$/.exec(fragment.trim());
  if (!m) return null;
  try {
    const key = await crypto.subtle.importKey("raw", fromB64Url(m[2]), { name: "AES-GCM" }, true, ["encrypt", "decrypt"]);
    return { epoch: Number(m[1]), key };
  } catch { return null; }
}

export const inviteUrl = (origin: string, inviteId: string, fragment: string): string =>
  `${origin.replace(/\/+$/, "")}/room/join?id=${encodeURIComponent(inviteId)}#${fragment}`;
