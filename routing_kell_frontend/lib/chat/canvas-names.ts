// What a canvas is called. The same rules as the server's backend/app/services/canvas_names.py: a title that is only an id (a UUID, a hash, a
// number, a key) is not a name, and a name is read from the document's own text instead: its <title> or first heading, else its first line.
// The server sends this as `label`; this copy is for lists from a server that does not yet (and keeps the picker readable meanwhile).

const UUID = /^[0-9a-f]{8}-?([0-9a-f]{4}-?){3}[0-9a-f]{12}$/i;
const HEX = /^[0-9a-f]{12,}$/i;
const NUMBERISH = /^[\d\s._#:-]+$/;
const UNTITLED = new Set(["untitled", "canvas", "document", "page", "web page", "html artifact", "artifact", "slides", "code", "result", "output"]);

export function isUglyTitle(title: string): boolean {
  const t = (title ?? "").trim();
  if (t.length < 3) return true;
  if (UUID.test(t) || HEX.test(t) || NUMBERISH.test(t)) return true;
  if (/^[\w-]{1,40}\.(html?|txt|md|tex)$/i.test(t) && !/[a-z]{4}/i.test(t.replace(/\.[a-z]+$/i, ""))) return true;
  if (!t.includes(" ") && t.length >= 10 && /\d/.test(t) && /[a-z]/i.test(t) && !/[aeiou][^aeiou\d]{1,4}[aeiou]/i.test(t.slice(0, 8))) return true;
  return false;
}

const plainWord = (kind: string) => (kind === "html" ? "Web page" : kind === "code" ? "Code" : kind === "latex" ? "LaTeX document" : "Document");

export function guessTitle(text: string, kind = "document"): string {
  const body = text ?? "";
  const tag = /<title[^>]*>([\s\S]*?)<\/title>/i.exec(body) ?? /<h[12][^>]*>([\s\S]*?)<\/h[12]>/i.exec(body);
  let cand = tag ? tag[1].replace(/<[^>]+>/g, "").trim() : "";
  if (!cand || isUglyTitle(cand) || UNTITLED.has(cand.toLowerCase())) {
    const h = /^\s{0,3}#{1,3}\s+(.+)$/m.exec(body);
    cand = h ? h[1].replace(/^#+\s*/, "").trim() : "";
  }
  if (!cand || isUglyTitle(cand)) {
    cand = "";
    for (const raw of body.split("\n")) {
      const line = raw.replace(/^[\s#*\-`>]+|[\s#*\-`>]+$/g, "");
      if (line.length >= 3 && !/^(import |from |def |<|\{|\\)/.test(line)) { cand = line; break; }
    }
  }
  cand = cand.replace(/\s+/g, " ").slice(0, 60).replace(/^[\s.:-]+|[\s.:-]+$/g, "");
  return cand.length >= 3 && !isUglyTitle(cand) ? cand : plainWord(kind);
}

/** The name to show: the stored title when it is a name, else one read from the text. */
export function labelFor(title: string, kind: string, body: string): string {
  const t = (title ?? "").trim();
  if (!isUglyTitle(t) && !UNTITLED.has(t.toLowerCase())) return t;
  return guessTitle(body, kind);
}
