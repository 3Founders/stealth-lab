/**
 * Pictures in the chat: sending one to a model, and showing one a model sends back.
 *
 * What it does, and the limits that are on purpose:
 *   - A picture you attach is re-drawn onto a canvas, scaled so its longest side is at most MAX_EDGE, and saved again as a JPEG. That keeps
 *     the request small and drops what the file carried besides the picture (camera and location tags). An animated GIF becomes its first frame.
 *   - Pictures stay on this device. A room event is text of at most 8,000 characters, so the room's server, and your other devices, get a note
 *     ("[image]") where the picture was, never the picture itself.
 *   - A model's reply can contain a picture as markdown: ![alt](url). A data: picture is drawn. A web (https) picture is NOT fetched until you
 *     click it: a model, or text it was shown, could point at an address that records who opened it, and a picture request is how data leaves.
 *     Anything else (http, javascript:, svg, a file) is shown as plain text.
 *   - Whether a model can read pictures is the model's business. kell does not know, so it sends the picture and, if the provider refuses,
 *     says so in the room (see `visionHint`).
 */
import type { ImageAttachment } from "./types";

export const MAX_IMAGES = 4;
export const MAX_FILE_BYTES = 10 * 1024 * 1024;
export const MAX_EDGE = 1568;
/** At most this many pictures go to the model in one request (the newest); older ones are replaced by "[image omitted]". */
export const MAX_IMAGES_PER_REQUEST = 3;
const MAX_DATA_URL_CHARS = 6_000_000;
export const ACCEPT = "image/png,image/jpeg,image/webp,image/gif";
const OK_TYPES = new Set(["image/png", "image/jpeg", "image/webp", "image/gif"]);

/** Why a file can't be attached, or null if it can. */
export function whyNotImage(file: { type: string; size: number; name?: string }, already: number): string | null {
  if (already >= MAX_IMAGES) return `At most ${MAX_IMAGES} pictures in one message.`;
  if (!OK_TYPES.has(file.type)) return `${file.name ? `${file.name} is` : "That is"} not a PNG, JPEG, WebP or GIF picture.`;
  if (file.size > MAX_FILE_BYTES) return `${file.name ? `${file.name} is` : "That picture is"} over ${MAX_FILE_BYTES / 1024 / 1024} MB.`;
  return null;
}

/** The size to draw a picture at: the same shape, longest side at most `max`, never enlarged. */
export function fitWithin(w: number, h: number, max = MAX_EDGE): { width: number; height: number } {
  const longest = Math.max(w, h);
  if (longest <= max || longest <= 0) return { width: Math.max(1, Math.round(w)), height: Math.max(1, Math.round(h)) };
  const k = max / longest;
  return { width: Math.max(1, Math.round(w * k)), height: Math.max(1, Math.round(h * k)) };
}

/** Reads a picture file, scales it down and re-encodes it. Needs a browser (canvas); it is not called in tests. */
export async function prepareImage(file: File): Promise<ImageAttachment> {
  const bitmap = await createImageBitmap(file).catch(() => { throw new Error(`${file.name || "That file"} could not be read as a picture.`); });
  const { width, height } = fitWithin(bitmap.width, bitmap.height);
  const canvas = document.createElement("canvas");
  canvas.width = width; canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("This browser can't prepare pictures.");
  ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, width, height); // a transparent picture becomes white, not black
  ctx.drawImage(bitmap, 0, 0, width, height);
  bitmap.close?.();
  const url = canvas.toDataURL("image/jpeg", 0.86);
  const data = url.slice(url.indexOf(",") + 1);
  if (!data) throw new Error("This browser can't prepare pictures.");
  return { kind: "image", mime: "image/jpeg", data, name: file.name || "picture", width, height };
}

export const dataUrlOf = (a: Pick<ImageAttachment, "mime" | "data">): string => `data:${a.mime};base64,${a.data}`;

export type Segment =
  | { t: "text"; s: string }
  | { t: "image"; alt: string; url: string; src: "data" | "remote" };

const IMG_MD = /!\[([^\]\n]{0,200})\]\(\s*([^\s)]{1,6000000})\s*(?:"[^"\n]*")?\)/g;
const DATA_IMG = /^data:image\/(?:png|jpeg|jpg|webp|gif);base64,[A-Za-z0-9+/=]+$/i;

/** Splits a message into text and pictures. Only data: pictures (png, jpeg, webp, gif) and https: addresses become pictures. */
export function splitImages(text: string): Segment[] {
  const out: Segment[] = [];
  let last = 0;
  for (const m of text.matchAll(IMG_MD)) {
    const url = m[2];
    const data = url.length <= MAX_DATA_URL_CHARS && DATA_IMG.test(url);
    let remote = false;
    if (!data) { try { const u = new URL(url); remote = u.protocol === "https:" && !u.username && !u.password; } catch { remote = false; } }
    if (!data && !remote) continue; // leave it as text
    if (m.index! > last) out.push({ t: "text", s: text.slice(last, m.index) });
    out.push({ t: "image", alt: m[1].trim(), url, src: data ? "data" : "remote" });
    last = m.index! + m[0].length;
  }
  if (last < text.length) out.push({ t: "text", s: text.slice(last) });
  return out.length ? out : [{ t: "text", s: text }];
}

/** Replaces every data: picture in a text with "[image]". Used before text goes to a room's server (too big) or back to a model (wasteful). */
export function withoutDataImages(text: string): string {
  return text.replace(IMG_MD, (all, _alt, url: string) => (url.startsWith("data:") ? "[image]" : all));
}

/** The text of a message as the room's server should store it: pictures replaced by a note. */
export function textForSync(text: string, attachments?: ImageAttachment[]): string {
  const base = withoutDataImages(text);
  const n = attachments?.length ?? 0;
  return n ? `${base}${base ? "\n\n" : ""}[${n} picture${n === 1 ? "" : "s"} attached on the sender's device]` : base;
}

/** A sentence to add when a provider's error looks like "this model can't read pictures". */
export function visionHint(errorText: string): string | null {
  return /image|vision|multimodal|modalit|content[- ]?part|unsupported.*(type|content)/i.test(errorText)
    ? "This model may not accept pictures. Choose a model that can read images under Models, or send the message without one."
    : null;
}

/** Host of an address, for "Load picture from …". */
export function hostOf(url: string): string { try { return new URL(url).host; } catch { return "the web"; } }
