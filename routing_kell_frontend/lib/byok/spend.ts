/**
 * A soft, client-side guard: the most assistant replies this browser will request for one account per local day. It only counts
 * requests (never keys, never text), so it may live in localStorage. It is a convenience, not a limit anyone is held to: the
 * provider bills the person directly, and the provider's own limits apply. kell does not pay for or see this usage.
 */
export const DEFAULT_DAILY_CAP = 50;
const MAX_CAP = 5000;

export interface KeyValue { getItem(k: string): string | null; setItem(k: string, v: string): void }

const day = (now: Date) => `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
const capKey = (subject: string) => `kel.byok.cap.${subject}`;
const useKey = (subject: string) => `kel.byok.used.${subject}`;

export function readCap(store: KeyValue, subject: string): number {
  try {
    const n = Number(store.getItem(capKey(subject)));
    return Number.isInteger(n) && n >= 1 && n <= MAX_CAP ? n : DEFAULT_DAILY_CAP;
  } catch { return DEFAULT_DAILY_CAP; }
}

export function writeCap(store: KeyValue, subject: string, cap: number): number {
  const n = Math.min(MAX_CAP, Math.max(1, Math.floor(Number(cap)) || DEFAULT_DAILY_CAP));
  try { store.setItem(capKey(subject), String(n)); } catch { /* storage blocked: the default applies */ }
  return n;
}

export function usedToday(store: KeyValue, subject: string, now = new Date()): number {
  try {
    const raw = JSON.parse(store.getItem(useKey(subject)) ?? "null") as { day?: string; n?: number } | null;
    return raw && raw.day === day(now) && Number.isInteger(raw.n) ? (raw.n as number) : 0;
  } catch { return 0; }
}

/** True when one more reply is within today's cap. */
export const withinCap = (store: KeyValue, subject: string, now = new Date()): boolean => usedToday(store, subject, now) < readCap(store, subject);

export function recordUse(store: KeyValue, subject: string, now = new Date()): number {
  const n = usedToday(store, subject, now) + 1;
  try { store.setItem(useKey(subject), JSON.stringify({ day: day(now), n })); } catch { /* ignore */ }
  return n;
}
