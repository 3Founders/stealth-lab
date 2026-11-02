// Follow-up suggestions: after an answer, kell may end with one line [[next: a | b | c]]. The page turns it into up to three buttons under
// the last answer and never shows the raw line. (Clickable follow-up suggestions are an idea from Open WebUI,
// https://github.com/open-webui/open-webui; no code copied.)
export const MAX_FOLLOW_UPS = 3;
const COMPLETE = /\s*\[\[next:\s*([^\]]*?)\s*\]\]\s*$/i;
const PARTIAL = /\s*\[\[(?:n(?:e(?:x(?:t(?::[^\]]*)?)?)?)?)?\]?$/i;

/** The text without its suggestion line, and the suggestions (at most three, trimmed, no empties, each 120 characters or fewer). */
export function splitNext(text: string): { text: string; next: string[] } {
  const m = COMPLETE.exec(text);
  if (!m) return { text, next: [] };
  const next = m[1].split("|").map((s) => s.trim()).filter((s) => s && s.length <= 120).slice(0, MAX_FOLLOW_UPS);
  return { text: text.slice(0, m.index).trimEnd(), next };
}

/** For text that is still arriving: also hides a suggestion line that has started but is not finished, so "[[ne" never flashes. */
export function withoutNext(text: string): string {
  const done = splitNext(text);
  if (done.next.length || COMPLETE.test(text)) return done.text;
  const open = text.lastIndexOf("[[");
  if (open >= 0 && PARTIAL.test(text.slice(Math.max(0, open - 1)))) return text.slice(0, open).trimEnd();
  return text;
}
