// Canvas: the documents, code and pages an agent produces in a conversation, kept as versions you can read, edit and restore. They are
// stored on this device, encrypted like your messages (lib/chat/store.ts, putDoc). A side panel with versions, copy and a preview is an idea
// from Open WebUI's artifacts and OpenAI's canvas (https://github.com/open-webui/open-webui); no code copied.
//
// An agent makes or revises one by writing a fenced block with the word "canvas" and a title:
//
//     ```canvas title="Launch plan" kind=document
//     ...the whole document...
//     ```
//
// A block with the same title in the same room is a new version of that canvas, not a new canvas. A fence of ~~~ works too, for text that
// itself contains ``` fences.
export type CanvasKind = "document" | "code" | "html";

export interface CanvasVersion { n: number; text: string; at: number; by: string; src?: string }
export interface Canvas { id: string; room: string; title: string; kind: CanvasKind; versions: CanvasVersion[]; createdAt: number }

export const MAX_VERSIONS = 50;
export const MAX_CANVAS_CHARS = 200_000;

export interface CanvasBlock { title: string; kind: CanvasKind; text: string; start: number; end: number }

const KINDS: CanvasKind[] = ["document", "code", "html"];
const FENCE = /(^|\n)(```|~~~)canvas([^\n]*)\n([\s\S]*?)\n\2[ \t]*(?=\n|$)/g;

/** Every canvas block in a message, with where it starts and ends, so the rest of the text can be shown around it. */
export function extractCanvasBlocks(text: string): CanvasBlock[] {
  const out: CanvasBlock[] = [];
  for (const m of text.matchAll(FENCE)) {
    const attrs = m[3] ?? "";
    const title = (/title\s*=\s*"([^"\n]{1,120})"/i.exec(attrs)?.[1] ?? /title\s*=\s*([^\s"]{1,120})/i.exec(attrs)?.[1] ?? "Untitled").trim() || "Untitled";
    const k = /kind\s*=\s*"?(\w+)"?/i.exec(attrs)?.[1]?.toLowerCase() as CanvasKind | undefined;
    const start = (m.index ?? 0) + m[1].length;
    out.push({ title, kind: k && KINDS.includes(k) ? k : "document", text: m[4].slice(0, MAX_CANVAS_CHARS), start, end: (m.index ?? 0) + m[0].length });
  }
  return out;
}

export type CanvasPart = { t: "text"; text: string } | { t: "canvas"; block: CanvasBlock };

/** The message as alternating text and canvas blocks, for drawing a card where each block was. */
export function splitCanvas(text: string): CanvasPart[] {
  const blocks = extractCanvasBlocks(text);
  if (!blocks.length) return [{ t: "text", text }];
  const out: CanvasPart[] = [];
  let at = 0;
  for (const b of blocks) {
    if (b.start > at) out.push({ t: "text", text: text.slice(at, b.start) });
    out.push({ t: "canvas", block: b });
    at = b.end;
  }
  if (at < text.length) out.push({ t: "text", text: text.slice(at) });
  return out;
}

export const latest = (c: Canvas): CanvasVersion => c.versions[c.versions.length - 1];

export function newCanvas(id: string, room: string, title: string, kind: CanvasKind, text: string, by: string, at = Date.now(), src?: string): Canvas {
  return { id, room, title, kind, createdAt: at, versions: [{ n: 1, text: text.slice(0, MAX_CANVAS_CHARS), at, by, ...(src ? { src } : {}) }] };
}

/** A new version on top. Nothing changes when the text equals the latest one, or when this message already made a version (a message
 * seen again after a reload must not add the same version twice). The oldest versions beyond the limit are dropped. */
export function addVersion(c: Canvas, text: string, by: string, at = Date.now(), src?: string): Canvas {
  const clipped = text.slice(0, MAX_CANVAS_CHARS);
  if (src && c.versions.some((v) => v.src === src)) return c;
  if (latest(c).text === clipped) return c;
  const versions = [...c.versions, { n: latest(c).n + 1, text: clipped, at, by, ...(src ? { src } : {}) }];
  return { ...c, versions: versions.slice(-MAX_VERSIONS) };
}

/** Makes an earlier version the newest again (as a new version, so nothing is lost). */
export function restoreVersion(c: Canvas, n: number, at = Date.now()): Canvas {
  const v = c.versions.find((x) => x.n === n);
  return v ? addVersion(c, v.text, "you", at) : c;
}

/** Adds what an agent's message made: a new canvas for a new title, a new version for a title this room already has. Returns only the
 * canvases that changed. */
export function captureFrom(canvases: Canvas[], m: { id: string; room: string; by: string; text: string; at: number }, newId: () => string): Canvas[] {
  const changed: Canvas[] = [];
  const current = [...canvases];
  for (const b of extractCanvasBlocks(m.text)) {
    const i = current.findIndex((c) => c.room === m.room && c.title.toLowerCase() === b.title.toLowerCase());
    if (i >= 0) {
      const next = addVersion(current[i], b.text, m.by, m.at, m.id);
      if (next !== current[i]) { current[i] = next; changed.push(next); }
    } else {
      const made = newCanvas(newId(), m.room, b.title, b.kind, b.text, m.by, m.at, m.id);
      current.push(made);
      changed.push(made);
    }
  }
  return changed;
}

/** The text to put in the message box to ask an agent to revise a canvas: what to change, and the current text in a canvas block. */
export function reviseRequest(c: Canvas, who = "@kell"): string {
  const fence = latest(c).text.includes("```") ? "~~~" : "```";
  return `${who} Revise the canvas "${c.title}". What to change: \n\n${fence}canvas title="${c.title}" kind=${c.kind}\n${latest(c).text}\n${fence}`;
}

export function fileNameFor(c: Canvas): string {
  const ext = c.kind === "html" ? "html" : c.kind === "code" ? "txt" : "md";
  return `${c.title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "canvas"}.${ext}`;
}

/** For text still arriving: a canvas block (finished or not yet closed) is shown as one short line, not as the whole document scrolling by. */
export function hideCanvasWhileStreaming(text: string): string {
  const FENCE_OPEN = /(^|\n)(```|~~~)canvas([^\n]*)(\n|$)/;
  let out = text;
  for (const b of [...extractCanvasBlocks(text)].reverse()) out = `${out.slice(0, b.start)}Canvas: ${b.title}${out.slice(b.end)}`;
  const open = FENCE_OPEN.exec(out);
  if (open) {
    const title = /title\s*=\s*"([^"\n]{1,120})"/i.exec(open[3] ?? "")?.[1];
    out = `${out.slice(0, (open.index ?? 0) + open[1].length)}Writing the canvas${title ? ` "${title}"` : ""}…`;
  }
  return out;
}
