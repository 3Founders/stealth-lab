// Privacy-first product analytics. Everything here is OFF unless NEXT_PUBLIC_PLAUSIBLE_DOMAIN is set at build time.
//
//   * Plausible (cloud or self-hosted) is cookieless and stores no personal data, so no consent banner is needed.
//     Still: this file documents what is sent, and docs/legal/COOKIES_AND_TRACKING.md must match it.
//   * Do Not Track / Global Privacy Control are respected: with either set, the script is never loaded and track() is a no-op.
//   * Events carry a name and a few small, coarse properties. NEVER pass free text a visitor typed (search queries, goals),
//     emails, ids or URLs; sanitize() drops anything that looks like that as a second line of defence.

export const PLAUSIBLE_DOMAIN = process.env.NEXT_PUBLIC_PLAUSIBLE_DOMAIN?.trim() || "";
export const PLAUSIBLE_SRC = process.env.NEXT_PUBLIC_PLAUSIBLE_SRC?.trim() || "https://plausible.io/js/script.tagged-events.js";

export type EventName =
  | "install_copy"       // visitor copied the install command
  | "sign_in_click"      // visitor clicked Sign in
  | "search_submit"      // visitor ran a search (never the query text)
  | "search_result"      // how many results came back, bucketed
  | "web_vital";         // Core Web Vitals: LCP, CLS, INP, TTFB

type Props = Record<string, string | number | boolean>;

export type PlausibleFn = ((name: string, opts?: { props?: Props }) => void) & { q?: unknown[] };

declare global {
  interface Window {
    plausible?: PlausibleFn;
  }
}

export function privacySignalOn(): boolean {
  if (typeof navigator === "undefined") return true;
  const n = navigator as Navigator & { globalPrivacyControl?: boolean; msDoNotTrack?: string };
  return n.doNotTrack === "1" || (window as unknown as { doNotTrack?: string }).doNotTrack === "1" || n.msDoNotTrack === "1" || n.globalPrivacyControl === true;
}

export function analyticsEnabled(): boolean {
  return Boolean(PLAUSIBLE_DOMAIN) && !privacySignalOn();
}

const LOOKS_PERSONAL = /@|https?:\/\/|\s{2,}|[0-9a-f]{8}-[0-9a-f]{4}-/i;

export function sanitize(props?: Props): Props | undefined {
  if (!props) return undefined;
  const out: Props = {};
  for (const [k, v] of Object.entries(props)) {
    if (typeof v === "string") {
      if (v.length > 40 || LOOKS_PERSONAL.test(v)) continue;   // free text / ids / urls never leave the browser
      out[k.slice(0, 30)] = v;
    } else if (typeof v === "number" || typeof v === "boolean") {
      out[k.slice(0, 30)] = v;
    }
  }
  return Object.keys(out).length ? out : undefined;
}

export function bucket(n: number): string {
  if (n <= 0) return "0";
  if (n <= 3) return "1-3";
  if (n <= 10) return "4-10";
  return "11+";
}

export function track(name: EventName, props?: Props): void {
  if (!analyticsEnabled() || typeof window === "undefined" || !window.plausible) return;
  try {
    window.plausible(name, { props: sanitize(props) });
  } catch {
    /* analytics must never break the page */
  }
}
