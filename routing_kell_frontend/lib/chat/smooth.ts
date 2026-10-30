/**
 * Smooth streaming text. A provider sends text in bursts (a few words, then a pause, then a long run), and showing each burst the
 * moment it lands looks jerky. The page instead keeps a "shown" position that chases the received text: it moves at a steady
 * reading pace when little is waiting, and faster the more is waiting, so it never falls far behind and catches up quickly once
 * the stream ends. This is only presentation: the posted message is always the full text, and nothing here changes what is sent.
 */
export const MIN_CPS = 40;       // characters per second when almost nothing is waiting: a calm reading pace
export const CATCH_UP = 6;       // the backlog is cleared at roughly this many times per second (a ~170 ms time constant)
export const MAX_FRAME_MS = 100; // a stalled tab must not reveal everything in one frame after it wakes

/** How far to show after `dtMs`, given the current (fractional) position and the length received. Never passes `target`. */
export function revealStep(shown: number, target: number, dtMs: number): number {
  if (shown >= target) return target;
  const dt = Math.min(Math.max(dtMs, 0), MAX_FRAME_MS) / 1000;
  const cps = Math.max(MIN_CPS, (target - shown) * CATCH_UP);
  return Math.min(target, shown + cps * dt);
}

/** Cuts `text` at a whole character (never between the two halves of an emoji) and, when it can, after a word. */
export function visiblePrefix(text: string, shown: number): string {
  let n = Math.floor(shown);
  if (n >= text.length) return text;
  const code = text.charCodeAt(n - 1);
  if (code >= 0xd800 && code <= 0xdbff) n -= 1;   // do not end on a high surrogate
  return text.slice(0, Math.max(0, n));
}
