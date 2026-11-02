/**
 * HTML artifacts in a room message: a fenced ```html block carrying a complete, self-contained page (a slide deck, a small
 * site, a chart). It is shown as a card and rendered ONLY when the person clicks Render, inside a sandboxed iframe with no
 * access to this page - the same click-to-load rule the pictures follow, because a page produced by a model must never run
 * on sight, and a trusted-looking artifact can still open a network connection.
 *
 * Size: the artifact is part of the message text, and a room event is capped at 8,000 characters server-side
 * (backend/app/services/rooms.py MAX_BODY). A larger block is left as plain text here rather than half-rendered.
 * Self-contained is the point: inline CSS and JS, pictures as data: URLs, no external scripts (the sandbox blocks the page
 * from reading this tab, not from the network, so the joiner kit tells agents to keep it self-contained).
 */
export const MAX_ARTIFACT_CHARS = 8000;

export interface Artifact {
  title: string;
  html: string;
}

export type ArtifactSegment = { t: "text"; text: string } | { t: "artifact"; artifact: Artifact };

const FENCE = /```html[ \t]*([^\n]*)\n([\s\S]*?)```/g;

/** The card's title: a label after the ```html fence (title="Deck"), else the page's <title>, else a plain name. */
export function artifactTitle(html: string, label = ""): string {
  const named = label.trim().replace(/^title\s*=\s*["']?/i, "").replace(/["']$/, "").trim();
  if (isRealName(named)) return named.slice(0, 80);
  const m = /<title[^>]*>([\s\S]*?)<\/title>/i.exec(html);
  const fromTitle = m ? m[1].replace(/<[^>]*>/g, "").trim().replace(/\s+/g, " ") : "";
  if (isRealName(fromTitle)) return fromTitle.slice(0, 80);
  const h = /<h1[^>]*>([\s\S]*?)<\/h1>/i.exec(html);
  const fromHeading = h ? h[1].replace(/<[^>]*>/g, "").trim().replace(/\s+/g, " ") : "";
  return isRealName(fromHeading) ? fromHeading.slice(0, 80) : "HTML artifact";
}

/** A label that reads as a name. A bare number, a file name like 1.html, or a key-looking string (hex, a UUID, a long token) is not one. */
export function isRealName(s: string): boolean {
  const t = s.trim();
  if (t.length < 2) return false;
  if (/^[\d\s._-]+$/.test(t)) return false;                                  // 3, 12, 1-2
  if (/^[\w-]+\.(html?|txt|md)$/i.test(t) && !/[a-z]{3}/i.test(t.replace(/\.[a-z]+$/i, ""))) return false;
  if (/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(t)) return false;  // a UUID
  if (/^[0-9a-f]{16,}$/i.test(t)) return false;                              // a hash
  if (/^[A-Za-z0-9_-]{20,}$/.test(t) && !/[aeiou]{1}[^aeiou]{0,4}[aeiou]/i.test(t.slice(0, 6))) return false; // a long token with no word in it
  return true;
}

/** The message split into ordinary text and the HTML artifacts it carries. Everything else is left exactly as written. */
export function splitArtifacts(text: string): ArtifactSegment[] {
  const out: ArtifactSegment[] = [];
  let last = 0;
  for (const m of text.matchAll(FENCE)) {
    const start = m.index ?? 0;
    if (start > last) out.push({ t: "text", text: text.slice(last, start) });
    const html = m[2].trim();
    if (html && html.length <= MAX_ARTIFACT_CHARS) out.push({ t: "artifact", artifact: { title: artifactTitle(html, m[1] || ""), html } });
    else out.push({ t: "text", text: m[0] });                         // too big (or empty): keep it readable, do not render it
    last = start + m[0].length;
  }
  if (last < text.length) out.push({ t: "text", text: text.slice(last) });
  return out.filter((s) => s.t === "artifact" || s.text.trim().length > 0);
}
